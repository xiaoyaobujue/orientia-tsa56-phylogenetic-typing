import { createHash } from "node:crypto";
import { createReadStream, readFileSync, readdirSync, statSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.resolve(scriptDir, "..");
const clientDir = path.resolve(projectDir, "frontend", "dist", "client");
const assetDir = path.join(clientDir, "assets");
const assetFiles = readdirSync(assetDir).filter((name) => statSync(path.join(assetDir, name)).isFile()).sort();
const proxyAssetSchema = "2";
const assetVersion = createHash("sha256")
  .update(`${assetFiles.join("\n")}\nproxy-schema:${proxyAssetSchema}`)
  .digest("hex")
  .slice(0, 12);

function addAssetVersions(source) {
  let versioned = source;
  for (const assetFile of assetFiles) {
    versioned = versioned
      .replaceAll(`"assets/${assetFile}"`, `"assets/${assetFile}?v=${assetVersion}"`)
      .replaceAll(`'assets/${assetFile}'`, `'assets/${assetFile}?v=${assetVersion}'`)
      .replaceAll(`\`assets/${assetFile}\``, `\`assets/${assetFile}?v=${assetVersion}\``)
      .replaceAll(`/assets/${assetFile}`, `/assets/${assetFile}?v=${assetVersion}`)
      .replaceAll(`./${assetFile}`, `./${assetFile}?v=${assetVersion}`);
  }
  return versioned;
}

function readOption(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const host = readOption("--hostname", "localhost");
const port = Number.parseInt(readOption("--port", "3200"), 10);
const upstreamHost = readOption("--upstream-host", "127.0.0.1");
const upstreamPort = Number.parseInt(readOption("--upstream-port", "3210"), 10);

const contentTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".gif", "image/gif"],
  [".ico", "image/x-icon"],
  [".jpeg", "image/jpeg"],
  [".jpg", "image/jpeg"],
  [".js", "application/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml; charset=utf-8"],
  [".webp", "image/webp"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
]);

function resolveStaticFile(urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath);
  } catch {
    return null;
  }
  if (decoded.includes("\0")) return null;
  const absolute = path.resolve(clientDir, `.${decoded}`);
  if (absolute !== clientDir && !absolute.startsWith(`${clientDir}${path.sep}`)) return null;
  try {
    return statSync(absolute).isFile() ? absolute : null;
  } catch {
    return null;
  }
}

function serveStatic(req, res, pathname) {
  if (req.method !== "GET" && req.method !== "HEAD") return false;
  const filePath = resolveStaticFile(pathname);
  if (!filePath) return false;

  const info = statSync(filePath);
  const extension = path.extname(filePath).toLowerCase();
  const transformedPayload = extension === ".js"
    ? Buffer.from(addAssetVersions(readFileSync(filePath, "utf8")))
    : null;
  const headers = {
    "Cache-Control": pathname.startsWith("/assets/")
      ? "public, max-age=31536000, immutable"
      : "public, max-age=3600",
    "Content-Length": String(transformedPayload?.length ?? info.size),
    "Content-Type": contentTypes.get(extension) ?? "application/octet-stream",
    "X-Content-Type-Options": "nosniff",
    "X-Orientia-Frontend-Mode": "production",
  };
  res.writeHead(200, headers);
  if (req.method === "HEAD") {
    res.end();
  } else if (transformedPayload) {
    res.end(transformedPayload);
  } else {
    createReadStream(filePath).pipe(res);
  }
  return true;
}

function proxyToVinext(req, res) {
  const upstreamHeaders = { ...req.headers };
  delete upstreamHeaders["accept-encoding"];
  const upstream = httpRequest(
    {
      host: upstreamHost,
      port: upstreamPort,
      method: req.method,
      path: req.url,
      headers: upstreamHeaders,
    },
    (upstreamResponse) => {
      const headers = { ...upstreamResponse.headers };
      headers["x-orientia-frontend-mode"] = "production";
      const contentType = String(upstreamResponse.headers["content-type"] ?? "");
      if (contentType.toLowerCase().startsWith("text/html")) {
        const chunks = [];
        upstreamResponse.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        upstreamResponse.on("end", () => {
          const body = addAssetVersions(Buffer.concat(chunks).toString("utf8"));
          const payload = Buffer.from(body);
          delete headers["content-encoding"];
          delete headers["content-length"];
          delete headers.etag;
          delete headers["transfer-encoding"];
          headers["cache-control"] = "no-store";
          headers["content-length"] = String(payload.length);
          res.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.statusMessage, headers);
          if (req.method === "HEAD") res.end();
          else res.end(payload);
        });
        return;
      }
      res.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.statusMessage, headers);
      upstreamResponse.pipe(res);
    },
  );
  upstream.on("error", () => {
    if (!res.headersSent) {
      res.writeHead(502, {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Orientia-Frontend-Mode": "production",
      });
    }
    res.end("Frontend rendering service is unavailable.");
  });
  req.pipe(upstream);
}

const server = createServer((req, res) => {
  const pathname = (req.url ?? "/").split("?", 1)[0];
  if (!serveStatic(req, res, pathname)) proxyToVinext(req, res);
});

server.listen(port, host, () => {
  console.log(`Orientia production frontend running at http://${host}:${port}`);
});
