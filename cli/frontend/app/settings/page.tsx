import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { getUser } from '../actions/tunnel-actions'
import DashboardLayout from '@/components/DashboardLayout'
import Settings from '@/components/Settings'

export default async function SettingsPage() {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) {
    redirect('/login')
  }
  
  const userData = await getUser(token.value)
  
  if (!userData) {
    redirect('/login')
  }
  
  return (
    <DashboardLayout user={userData} currentPage="settings">
      <Settings user={userData} />
    </DashboardLayout>
  )
}
