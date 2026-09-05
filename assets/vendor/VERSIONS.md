# Vendored front-end dependencies

Everything the pages load is served from this repository. No CDN: the site
sits behind Cloudflare Access and a third-party script host is both a
runtime dependency and a data-exfiltration path we do not need.

| File | Library | Version | Licence | SHA-256 |
|---|---|---|---|---|
| `chart.umd.min.js` | Chart.js | 4.4.0 | MIT | `321e3a3fa98da4aaa957d10be57cbb514de0989eed8f9d726b5d05902cd01904` |

## Provenance of chart.umd.min.js

The egress policy for the build environment denied `cdnjs.cloudflare.com`,
`cdn.jsdelivr.net` and `unpkg.com`, so the file was taken from the npm
registry tarball instead, which is the artefact those CDNs mirror:

    https://registry.npmjs.org/chart.js/-/chart.js-4.4.0.tgz
    sha256 5563cc3d87d1ddda7190f56b9a689e30da95c3fde7c7a94e8b566f85b47e6667

Inside the tarball the minified UMD build is `package/dist/chart.umd.js`
(cdnjs publishes the same file under the name `chart.umd.min.js`). The
header of the vendored file reads `Chart.js v4.4.0 … Released under the MIT
License`. Verify with:

    sha256sum assets/vendor/chart.umd.min.js

## Upgrading

Stay on 4.4.x. Fetch the new tarball, copy `package/dist/chart.umd.js` over
`chart.umd.min.js`, update the hash above, and re-run `tests/test_render.py`
(it checks the version string in the file header against this table).
