import { createHandler } from "./service.mjs";

// Application logs contain only the explicit operational allowlist in service.mjs.
Deno.serve(createHandler({ env: Deno.env.toObject(), fetch: globalThis.fetch,
  log: event => console.log(JSON.stringify(event)),
  waitUntil: task => EdgeRuntime.waitUntil(task) }));
