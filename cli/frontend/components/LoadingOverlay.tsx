'use client'

interface LoadingOverlayProps {
  message?: string
}

export default function LoadingOverlay({ message = 'Processing...' }: LoadingOverlayProps) {
  return (
    <div className="loading-overlay-fullscreen">
      <div className="loading-card">
        <div className="loading-spinner"></div>
        <p>{message}</p>
      </div>
    </div>
  )
}
