/**
 * Cloudflare Worker: reverse proxy for the Telegram Bot API.
 *
 * The bot (running on a server where api.telegram.org is filtered) sends its
 * requests to this Worker's domain instead. The Worker runs on Cloudflare's
 * edge (unfiltered) and forwards them to api.telegram.org.
 *
 * Only Bot API paths are allowed:
 *   /bot<token>/<method>        -> https://api.telegram.org/bot<token>/<method>
 *   /file/bot<token>/<path>     -> https://api.telegram.org/file/bot<token>/<path>
 *
 * Configure the bot with:
 *   TELEGRAM_API_BASE_URL = https://tgapi.paanapay.com/bot
 */
export default {
  async fetch(request) {
    const url = new URL(request.url);

    // Health check / block anything that isn't a Bot API call.
    if (url.pathname === "/" || url.pathname === "") {
      return new Response("ok", { status: 200 });
    }
    if (!url.pathname.startsWith("/bot") && !url.pathname.startsWith("/file/bot")) {
      return new Response("Not found", { status: 404 });
    }

    url.hostname = "api.telegram.org";
    url.protocol = "https:";
    url.port = "";

    // Preserve method, headers and body from the original request.
    const upstream = new Request(url.toString(), request);
    return fetch(upstream);
  },
};
