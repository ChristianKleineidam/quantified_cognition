"""Profile timers dispatch file/network jobs without using the collection queue."""

import threading

from aqt import gui_hooks
from aqt.qt import QTimer

from .backend import UpdateCancelled
from .background import run_file_job
from .google_health_api import sync_google_health
from .oura_api import sync_oura
from .research import SharingConfig
from .service import ServiceConfig
from .submissions import retry_deletion


class NetworkUpdater:
    def __init__(self, main, addon_name):
        self.main, self.addon_name = main, addon_name
        self.busy = False
        self.generation = 0
        self.cancelled = None
        self.timer = QTimer(main)
        self.timer.setInterval(5 * 60 * 1000)
        self.timer.timeout.connect(self.check)
        gui_hooks.profile_did_open.append(self.start)
        gui_hooks.profile_will_close.append(self.stop)

    def start(self):
        self.timer.start()
        QTimer.singleShot(3000, self.check)

    def stop(self):
        self.timer.stop()
        self.generation += 1
        self.busy = False
        if self.cancelled is not None:
            self.cancelled.set()

    def check(self):
        if self.busy or self.main.col is None:
            return
        source = self.main.col.path
        generation = self.generation
        manager, name = self.main.addonManager, self.addon_name
        self.busy = True
        cancelled = self.cancelled = threading.Event()

        def check_cancelled():
            if cancelled.is_set():
                raise UpdateCancelled("The profile closed or changed.")

        def task():
            # A previously requested deletion is retried even after a service
            # config change. Its original HTTPS endpoint is stored in the vault.
            try:
                retry_deletion(source)
            except Exception:
                pass  # Durable request remains queued; UI exposes pending state.
            check_cancelled()
            config = manager.getConfig(name)
            services = ServiceConfig.from_config(config)
            changed = False
            if services.base_url:
                for sync in (sync_oura, sync_google_health):
                    check_cancelled()
                    try:
                        result = sync(
                            source,
                            services,
                            SharingConfig.from_config(config),
                            check_cancelled=check_cancelled,
                        )
                        changed = changed or bool(
                            result is not None
                            and (
                                result.changed_records or getattr(result, "metadata_changed", False)
                            )
                        )
                    except UpdateCancelled:
                        raise
                    except Exception:
                        # Connections persist their own errors/backoff for the health screen.
                        pass
            return changed

        def done(_result):
            if generation == self.generation:
                self.busy = False
                if _result is True and self.main.col is not None and self.main.col.path == source:
                    from .ui import notify_insights_updated

                    notify_insights_updated(source)

        # No Qt or live collection references are used inside the network job.
        # Owned OS workers can finish credential rotation after a profile closes.
        run_file_job(task, done, done)
