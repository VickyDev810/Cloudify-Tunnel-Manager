'use client'

interface RouteManagerProps {
  tunnelsData: any
}

export default function RouteManager({ tunnelsData }: RouteManagerProps) {
  const routes: any[] = []
  
  tunnelsData?.tunnels?.forEach((tunnel: any) => {
    tunnel.routes?.forEach((route: any) => {
      routes.push({
        tunnel: tunnel.name,
        domain: route.domain,
        service: route.service,
        status: tunnel.status
      })
    })
  })

  return (
    <div className="glass-card">
      <h2>Routes Overview</h2>
      {routes.length === 0 ? (
        <p style={{ color: 'var(--text-dim)' }}>No routes configured</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Domain</th>
              <th>Service</th>
              <th>Tunnel</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {routes.map((route, idx) => (
              <tr key={idx}>
                <td><span className="route-domain">{route.domain}</span></td>
                <td>{route.service}</td>
                <td>{route.tunnel}</td>
                <td>
                  <span className={`tunnel-status ${route.status?.includes('running') ? 'running' : 'stopped'}`}>
                    {route.status?.includes('running') ? 'Active' : 'Inactive'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
