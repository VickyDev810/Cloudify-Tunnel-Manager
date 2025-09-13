'use client'

interface SidebarProps {
  currentPage: string
  onNavigate: (page: string) => void
  onLogout: () => void
}

export default function Sidebar({ currentPage, onNavigate, onLogout }: SidebarProps) {
  return (
    <nav className="sidebar">
      <div className="sidebar-header">
        <h2>CF Tunnel</h2>
      </div>
      <ul className="nav-menu">
        <li className="nav-item">
          <a 
            href="#" 
            className={`nav-link ${currentPage === 'dashboard' ? 'active' : ''}`}
            onClick={(e) => { e.preventDefault(); onNavigate('dashboard') }}
          >
            <span className="nav-icon">📊</span>
            Dashboard
          </a>
        </li>
        <li className="nav-item">
          <a 
            href="#" 
            className={`nav-link ${currentPage === 'tunnels' ? 'active' : ''}`}
            onClick={(e) => { e.preventDefault(); onNavigate('tunnels') }}
          >
            <span className="nav-icon">🚇</span>
            Tunnels
          </a>
        </li>
        <li className="nav-item">
          <a 
            href="#" 
            className={`nav-link ${currentPage === 'routes' ? 'active' : ''}`}
            onClick={(e) => { e.preventDefault(); onNavigate('routes') }}
          >
            <span className="nav-icon">🔀</span>
            Routes
          </a>
        </li>
        <li className="nav-item">
          <a 
            href="#" 
            className={`nav-link ${currentPage === 'settings' ? 'active' : ''}`}
            onClick={(e) => { e.preventDefault(); onNavigate('settings') }}
          >
            <span className="nav-icon">⚙️</span>
            Settings
          </a>
        </li>
        
        <li className="nav-item" style={{ marginTop: 'auto', paddingTop: '2rem' }}>
          <a 
            href="#" 
            className="nav-link"
            onClick={(e) => { e.preventDefault(); onLogout() }}
          >
            <span className="nav-icon">🚪</span>
            Logout
          </a>
        </li>
      </ul>
    </nav>
  )
}
