# Browser barcode decoder

`zxing-library-0.21.3.min.js` is the unmodified `package/umd/index.min.js` from the official npm package `@zxing/library@0.21.3`.

- Upstream: https://github.com/zxing-js/library
- Package: https://registry.npmjs.org/@zxing/library/-/library-0.21.3.tgz
- Official tarball integrity (verified before extraction): `sha512-hZHqFe2JyH/ZxviJZosZjV+2s6EDSY0O24R+FQmlWZBZXP9IqMo7S3nb3+2LBWxodJQkSurdQGnqE7KXqrYgow==`
- Licence: Apache-2.0, included in `ZXING-LICENSE`.

The decoder is served by this application; camera frames stay in the browser. Only the decoded student barcode is submitted to the authenticated attendance endpoint. Camera scanning is explicitly started, reads Code 128 student tags, and stops after a single tag or on navigation/page hiding. A USB/Bluetooth keyboard scanner remains available without camera permission.
