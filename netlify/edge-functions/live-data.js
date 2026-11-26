const UPSTREAM_BASE =
  "https://raw.githubusercontent.com/jorkerkoma/a-share-cash-mgmt/main/dist/data";

const EXCEL_FILENAME = "A股现金管理汇总.xlsx";

const ROUTES = {
  "/data/meta.json": {
    file: "meta.json",
    contentType: "application/json; charset=utf-8",
  },
  "/data/records.json": {
    file: "records.json",
    contentType: "application/json; charset=utf-8",
  },
  "/data/all.xlsx": {
    file: encodeURIComponent(EXCEL_FILENAME),
    contentType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    disposition: `attachment; filename="cash_mgmt_all.xlsx"; filename*=UTF-8''${encodeURIComponent(
      EXCEL_FILENAME
    )}`,
  },
};

export default async (request, context) => {
  const url = new URL(request.url);
  const spec = ROUTES[url.pathname];
  if (!spec) {
    return context.next();
  }

  try {
    const upstream = await fetch(`${UPSTREAM_BASE}/${spec.file}`, {
      headers: {
        "User-Agent": "ashare-cash-radar-edge/1.0",
      },
    });
    if (!upstream.ok) {
      return context.next();
    }

    const headers = new Headers({
      "Content-Type": spec.contentType,
      "Cache-Control": "public, max-age=300",
      "Netlify-CDN-Cache-Control":
        "public, s-maxage=300, stale-while-revalidate=600",
      "X-Data-Source": "live-github",
    });
    if (spec.disposition) {
      headers.set("Content-Disposition", spec.disposition);
    }

    return new Response(upstream.body, {
      status: 200,
      headers,
    });
  } catch (_err) {
    return context.next();
  }
};

export const config = {
  path: ["/data/meta.json", "/data/records.json", "/data/all.xlsx"],
  cache: "manual",
  onError: "bypass",
};
