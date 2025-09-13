'use client'

import { useRouter } from 'next/navigation'
import DashboardLayout from './DashboardLayout'
import { shutdown } from '../app/actions/tunnel-actions'
import { useState, useTransition } from 'react'

interface DashboardClientProps {
  initialData: any
  user: any
}

export default function DashboardClient({ initialData, user }: DashboardClientProps) {
  const router = useRouter()
  const tunnelsData = initialData
  const [shutdownMsg, setShutdownMsg] = useState<string>('')
  const [isPending, startTransition] = useTransition()

  const handleShutdown = () => {
    setShutdownMsg('')
    startTransition(async () => {
      const result = await shutdown()
      setShutdownMsg(result.success ? 'Shutdown triggered' : result.error || 'Failed to shutdown')
    })
  }

  return (
    <DashboardLayout user={user} currentPage="dashboard">
      <div>
        <button className="btn btn-danger btn-small" style={{ float: 'right', marginBottom: '1rem' }} onClick={handleShutdown} disabled={isPending}>
          {isPending ? 'Shutting down...' : 'Shutdown'}
        </button>
        {shutdownMsg && <div style={{ color: 'var(--danger)', marginBottom: '1rem' }}>{shutdownMsg}</div>}
        <div className="stats-grid">
          <div className="stat-card">
            <div className="stat-value">{tunnelsData?.tunnels?.length || 0}</div>
            <div className="stat-label">Active Tunnels</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">
              {tunnelsData?.tunnels?.reduce((acc: number, t: any) => acc + (t.routes?.length || 0), 0) || 0}
            </div>
            <div className="stat-label">Configured Routes</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">
              {tunnelsData?.tunnels?.filter((t: any) => t.status?.includes('running')).length || 0}
            </div>
            <div className="stat-label">Running Services</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">1</div>
            <div className="stat-label">Total Users</div>
          </div>
        </div>

        <div className="glass-card">
          <h2>Recent Activity</h2>
          {tunnelsData?.tunnels?.length > 0 ? (
            <div>
              {tunnelsData.tunnels.slice(0, 5).map((tunnel: any) => (
                <div key={tunnel.name} style={{ padding: '0.75rem 0', borderBottom: '1px solid var(--glass-border)' }}>
                  <span style={{ color: 'var(--text)' }}>Tunnel: {tunnel.name}</span>
                  <span className={`tunnel-status ${tunnel.status?.includes('running') ? 'running' : 'stopped'}`} style={{ marginLeft: '1rem' }}>
                    {tunnel.status || 'stopped'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="status-box info">
              <p><strong>Welcome to Cloudflare Tunnel Manager!</strong></p>
              <p style={{ marginTop: '0.5rem' }}>Get started by creating your first tunnel</p>
              <button 
                className="btn btn-primary btn-small" 
                style={{ marginTop: '1rem' }}
                onClick={() => router.push('/tunnels')}
              >
                Go to Tunnels
              </button>
            </div>
          )}
        </div>
      </div>
    </DashboardLayout>
  )
}
