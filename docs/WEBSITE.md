# Public project website

The public information website uses **Cloudflare Pages**, with the custom domain
`quantifiedcognition.jollyjoyjourney.com`. It is a static information site;
publishing it does not enable Oura, Google Health or research uploads.

Published on **4 November 2026** at
[quantifiedcognition.jollyjoyjourney.com](https://quantifiedcognition.jollyjoyjourney.com/).
The custom domain and certificate are active. The
[privacy policy](https://quantifiedcognition.jollyjoyjourney.com/privacy/) and
[research information](https://quantifiedcognition.jollyjoyjourney.com/research/)
are public. `site/deployment.json` records the verified deployment without credentials.

The website source lives in `site/`. `tools/build_website.py` renders the canonical
privacy policy and participant information directly from `docs/`, and the
reader-facing Methods page from `site/methods.md`. It also publishes exact
downloadable Markdown copies of those three pages. The research design,
operating plan and implementation controls stay in the repository and are not
website build inputs. Website hosting privacy is described separately in
`site/website-privacy.md`; the frozen study-consent documents are not rewritten
by the website build.

## Build and publish

Use Python 3.10 or later and `site/requirements.txt`. The Anki development
environment already contains Python-Markdown through aqt.

```powershell
python -m pip install -r site/requirements.txt
python tools/build_website.py --zip dist/quantified-cognition-website.zip
```

The output is `dist/website/`. The ZIP contains only the named public pages,
stylesheet, icon, license, canonical documents, sitemap and static headers.
The build refuses unexpected files in its output directory. It does not include
the add-on, database, credentials, profiles, private benchmarks or CSV exports.

In Cloudflare, open the **quantified-cognition** Pages project, create a production
deployment and upload `dist/quantified-cognition-website.zip`. Deploy the contents
of the ZIP, not a parent folder. This project uses **Direct Upload**; there is no
GitHub Actions workflow, GitHub repository access grant, Worker function, website
data store or automatic deployment trigger. Build and publish explicitly whenever
the website's source documents change.

The first deployment used the authorized `cf` CLI session and Cloudflare's
Pages asset API because browser uploads were unavailable. The Pages upload
manifest includes only verified build assets, with `_headers` supplied separately.
Cloudflare's `@cloudflare/deploy-helpers` computes the Pages content hashes;
the upload does not include a Worker or Functions bundle. CLI credentials are
kept by Cloudflare's CLI and are never copied into the project or website.

The Cloudflare Free plan serves the static site. Keep optional analytics and paid
features off. The existing company domain and email routing use independent DNS
records; the site needs only its own subdomain CNAME. The project must list the
custom domain before its DNS record is activated. HTTPS provisioning can take time.

The `no-transform` cache directive preserves the script-free pages and contact
emails. Cloudflare's automatic email obfuscation otherwise rewrites those emails
and injects a decoding script that the site's Content Security Policy blocks.
Keep this directive when changing the response headers.

## Checks

Check the homepage and policy/research/contact pages at desktop and phone widths,
local links, downloadable source-document equality, the 404 page and the public
HTTPS response headers. Confirm no analytics script or upload/enrollment form was
introduced. Keep all three online feature flags false. Publishing these pages
does not establish provider approval, accepted study enrollment or a verified
research backend deployment.

The 4 November live check verified all 15 public files against the build over
HTTPS, including exact downloadable source documents, security headers and the
custom 404 response. `_headers` is deployment configuration, the sixteenth
build file. The contact page was also checked in the browser after preventing
email rewriting.

The former `/operations/` and `/research-controls/` pages and the downloads
`RESEARCH_OPERATIONS.md`, `RESEARCH_CONTROLS.md` and `RESEARCH_DESIGN.md` are
excluded from the current site. They should return the custom 404 page. When
upgrading an older local website build, use a fresh output directory or remove
only those retired generated files before rebuilding; the build continues to
refuse unexpected leftovers.

The repository remains private until the separately requested public release.
The site's footer therefore links to its software license and operator contact
rather than offering an inaccessible source-repository or AnkiWeb download link.
