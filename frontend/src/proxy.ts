import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Next.js 16 Proxy: runs before request completion.
// 1. Adds the backend API key to /api requests on the server when BACKEND_API_KEY is configured.
// 2. Implements the route guard when NEXT_PUBLIC_AUTH_GUARD is enabled ('1' or 'true').
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // 1. Backend API Key proxying for /api requests
  if (pathname.startsWith("/api")) {
    const key = process.env.BACKEND_API_KEY;
    if (!key || request.headers.has("x-api-key")) {
      return NextResponse.next();
    }
    const headers = new Headers(request.headers);
    headers.set("x-api-key", key);
    return NextResponse.next({ request: { headers } });
  }

  // 2. Route Guard (soft mode controlled by NEXT_PUBLIC_AUTH_GUARD)
  const authGuardEnabled =
    process.env.NEXT_PUBLIC_AUTH_GUARD === "1" ||
    process.env.NEXT_PUBLIC_AUTH_GUARD === "true";

  if (authGuardEnabled) {
    const isPublicRoute =
      pathname === "/login" ||
      pathname.startsWith("/login/") ||
      pathname === "/pending" ||
      pathname.startsWith("/pending/");

    const hasSessionCookie = request.cookies.has("qs_session");

    if (!isPublicRoute && !hasSessionCookie) {
      const loginUrl = new URL("/login", request.url);
      if (pathname !== "/") {
        loginUrl.searchParams.set("redirect", pathname);
      }
      return NextResponse.redirect(loginUrl);
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match all request paths except for:
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico, icon.png
     * - public files with extensions (.svg, .png, .jpg, .jpeg, .webp, .ico, .txt)
     */
    "/((?!_next/static|_next/image|favicon.ico|icon.png|.*\\.(?:svg|png|jpg|jpeg|webp|ico|txt)$).*)",
  ],
};
