'use client'

import { createContext, useContext, useState, useCallback } from 'react'

interface Notification {
  id: string
  message: string
  type: 'success' | 'error' | 'warning' | 'info'
}

interface NotificationContextType {
  showNotification: (message: string, type: 'success' | 'error' | 'warning' | 'info') => void
}

const NotificationContext = createContext<NotificationContextType | undefined>(undefined)

export function useNotification() {
  const context = useContext(NotificationContext)
  if (!context) {
    throw new Error('useNotification must be used within a NotificationProvider')
  }
  return context
}

export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const [notifications, setNotifications] = useState<Notification[]>([])

  const showNotification = useCallback((message: string, type: 'success' | 'error' | 'warning' | 'info' = 'info') => {
    const id = Date.now().toString()
    setNotifications(prev => [...prev, { id, message, type }])
    
    setTimeout(() => {
      setNotifications(prev => prev.filter(n => n.id !== id))
    }, 3000)
  }, [])

  return (
    <NotificationContext.Provider value={{ showNotification }}>
      {children}
      <div className="notification-container">
        {notifications.map(notification => (
          <div 
            key={notification.id} 
            className={`notification show ${notification.type}`}
          >
            {notification.message}
          </div>
        ))}
      </div>
    </NotificationContext.Provider>
  )
}
