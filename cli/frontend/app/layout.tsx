import type { Metadata } from 'next'
import './globals.css'
import { NotificationProvider } from '@/components/NotificationProvider'

export const metadata: Metadata = {
  title: 'Cloudflare Tunnel Manager',
  description: 'Manage your Cloudflare tunnels with ease',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>
        <div className="bg-animation"></div>
        <NotificationProvider>
          {children}
        </NotificationProvider>
      </body>
    </html>
  )
}
