'use client'

interface DeleteConfirmModalProps {
  isOpen: boolean
  title: string
  message: string
  itemName: string
  onConfirm: () => void
  onCancel: () => void
  loading?: boolean
}

export default function DeleteConfirmModal({ 
  isOpen, 
  title, 
  message, 
  itemName,
  onConfirm, 
  onCancel,
  loading = false
}: DeleteConfirmModalProps) {
  if (!isOpen) return null

  return (
    <div className="modal active">
      <div className="modal-content">
        <div className="modal-header">
          <h2>{title}</h2>
          <button className="modal-close" onClick={onCancel} disabled={loading}>×</button>
        </div>
        
        <div className="status-box warning" style={{ marginBottom: '1.5rem' }}>
          <p style={{ fontSize: '1.1rem', fontWeight: 'bold', marginBottom: '0.5rem' }}>⚠️ Warning</p>
          <p>{message}</p>
          {itemName && (
            <p style={{ marginTop: '0.5rem' }}>
              <strong>Item to be deleted:</strong> <span style={{ color: 'var(--error)' }}>{itemName}</span>
            </p>
          )}
        </div>
        
        <p style={{ marginBottom: '1.5rem', color: 'var(--text-dim)' }}>
          This action cannot be undone. Are you sure you want to proceed?
        </p>
        
        <div className="btn-group">
          <button 
            className={`btn btn-danger ${loading ? 'loading-state' : ''}`}
            onClick={onConfirm}
            disabled={loading}
          >
            Yes, Delete
          </button>
          <button 
            className="btn btn-secondary" 
            onClick={onCancel}
            disabled={loading}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
