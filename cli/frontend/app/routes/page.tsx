import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { getTunnels, getUser } from '../actions/tunnel-actions'
import DashboardLayout from '@/components/DashboardLayout'
import RouteManager from '@/components/RouteManager'

export default async function RoutesPage() {
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
    <DashboardLayout user={userData} currentPage="routes">
      <RouteManager tunnelsData={tunnelsData} />
    </DashboardLayout>
  )
}
