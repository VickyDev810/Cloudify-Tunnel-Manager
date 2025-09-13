import { redirect } from 'next/navigation'
import { cookies } from 'next/headers'
import { verifyAuth, checkSetup } from './actions/tunnel-actions'

export default async function Home() {
  // First check setup status
  const setupStatus = await checkSetup()
  
  if (setupStatus && setupStatus.needs_setup) {
    // No users exist - redirect to setup wizard
    redirect('/setup')
  }
  
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) {
    redirect('/login')
  }
  
  const isValid = await verifyAuth(token.value)
  
  if (isValid) {
    redirect('/dashboard')
  } else {
    redirect('/login')
  }
}
