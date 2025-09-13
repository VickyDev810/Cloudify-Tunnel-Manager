import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import DashboardClient from '@/components/DashboardClient'
import { getTunnels, getUser } from '../actions/tunnel-actions'

export default async function DashboardPage() {
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
  
  return <DashboardClient initialData={tunnelsData} user={userData} />
}
