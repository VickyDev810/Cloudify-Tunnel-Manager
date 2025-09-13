'use client'

import { useRouter } from 'next/navigation'
import { logout } from '@/app/actions/tunnel-actions'
import Sidebar from './Sidebar'

interface DashboardLayoutProps {
  children: React.ReactNode
  user: any
  currentPage: string
}

export default function DashboardLayout({ children, user, currentPage }: DashboardLayoutProps) {
  const router = useRouter()

  const handleLogout = async () => {
    await logout()
    router.push('/login')
  }

  const handleNavigate = (page: string) => {
    router.push(`/${page}`)
  }

  const pages: { [key: string]: string } = {
    dashboard: 'Dashboard',
    tunnels: 'Tunnels Management',
    routes: 'Routes Overview',
    settings: 'System Settings'
  }

  return (
    <div className="main-layout">
      <Sidebar currentPage={currentPage} onNavigate={handleNavigate} onLogout={handleLogout} />
      
      <main className="main-content">
        <div className="topbar">
          <div className="topbar-title">{pages[currentPage]}</div>
          <div className="topbar-user">
            <div className="user-info">
              <div className="user-email">{user?.username || 'User'}</div>
              <div className="user-role">User</div>
            </div>
          </div>
        </div>

        {children}
      </main>
    </div>
  )
}
