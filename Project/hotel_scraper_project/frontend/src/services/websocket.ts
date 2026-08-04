import type { ScrapeProgress, ScrapeRequest } from '@/types'

export class WebSocketService {
  private ws: WebSocket | null = null
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5
  private reconnectDelay = 2000
  private messageHandlers: ((data: ScrapeProgress) => void)[] = []

  constructor(private url: string) {}

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(this.url)

        this.ws.onopen = () => {
          console.log('WebSocket connected')
          this.reconnectAttempts = 0
          resolve()
        }

        this.ws.onmessage = (event) => {
          try {
            const data: ScrapeProgress = JSON.parse(event.data)
            this.messageHandlers.forEach((handler) => handler(data))
          } catch (error) {
            console.error('Failed to parse WebSocket message:', error)
          }
        }

        this.ws.onerror = (error) => {
          console.error('WebSocket error:', error)
          reject(error)
        }

        this.ws.onclose = () => {
          console.log('WebSocket disconnected')
          this.attemptReconnect()
        }
      } catch (error) {
        reject(error)
      }
    })
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      this.reconnectAttempts++
      console.log(`Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`)
      setTimeout(() => {
        this.connect().catch(() => {
          console.error('Reconnection failed')
        })
      }, this.reconnectDelay)
    }
  }

  send(data: ScrapeRequest): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    } else {
      console.error('WebSocket is not connected')
    }
  }

  onMessage(handler: (data: ScrapeProgress) => void): void {
    this.messageHandlers.push(handler)
  }

  removeMessageHandler(handler: (data: ScrapeProgress) => void): void {
    this.messageHandlers = this.messageHandlers.filter((h) => h !== handler)
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    this.messageHandlers = []
  }

  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }
}

// Auto-detect WebSocket URL if not provided in env
function getWebSocketUrl() {
  const envUrl = import.meta.env.VITE_WS_URL
  if (envUrl) return envUrl

  // If running in browser, construct WS URL from current location
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return `${protocol}//${host}`
}

const wsUrl = getWebSocketUrl()
const cleanWsUrl = wsUrl.endsWith('/') ? wsUrl.slice(0, -1) : wsUrl

export const scraperWebSocket = new WebSocketService(`${cleanWsUrl}/ws/scrape`)
