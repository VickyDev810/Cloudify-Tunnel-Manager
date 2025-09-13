// FILE: middleware.ts (UPDATED)
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export function middleware(request: NextRequest) {
  const token = request.cookies.get('cf_tunnel_auth_token')
  const isLoginPage = request.nextUrl.pathname === '/login'
  const isSetupPage = request.nextUrl.pathname === '/setup'
  const isRootPage = request.nextUrl.pathname === '/'
  
  // Allow access to setup page without authentication
  if (isSetupPage) {
    return NextResponse.next()
  }
  
  // If no token and trying to access protected routes
  if (!token && !isLoginPage && !isRootPage) {
    return NextResponse.redirect(new URL('/login', request.url))
  }
  
  // If has token and trying to access login page
  if (token && isLoginPage) {
    return NextResponse.redirect(new URL('/dashboard', request.url))
  }
  
  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico).*)'],
}