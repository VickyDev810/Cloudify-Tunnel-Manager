import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { getTunnels, getUser } from '../actions/tunnel-actions'
import DashboardLayout from '@/components/DashboardLayout'
import TunnelManager from '@/components/TunnelManager'

export default async function TunnelsPage() {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) {
    redirect('/login')
  }
  
  const [tunnelsData, userData] = await Promise.all([
    getTunnels(token.value),
    getUser(token.value)
  ])
  
  if (!userData) {
    redirect('/login')
  }
  
  return (
    <DashboardLayout user={userData} currentPage="tunnels">
      <TunnelManager initialTunnels={tunnelsData?.tunnels || []} />
    </DashboardLayout>
  )
}
