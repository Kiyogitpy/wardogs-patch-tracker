import { createContext, useContext, useEffect, useState } from 'react'
import { clearWebhook, forgetWebhookUnlessOwnedBy } from './account'
import { getMe, type Me } from './api'

export type MeState = { state: 'loading' } | { state: 'out' } | { state: 'in'; me: Me }

const Ctx = createContext<{ me: MeState; signedOut: () => void }>({ me: { state: 'loading' }, signedOut: () => {} })

export function MeProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<MeState>({ state: 'loading' })
  useEffect(() => {
    const ctl = new AbortController()
    getMe(ctl.signal).then(
      (m) => {
        forgetWebhookUnlessOwnedBy(m?.discord_id ?? null)
        setMe(m ? { state: 'in', me: m } : { state: 'out' })
      },
      // The API being down is not a reason to hide the whole site: treat it as signed out.
      () => !ctl.signal.aborted && setMe({ state: 'out' }),
    )
    return () => ctl.abort()
  }, [])
  return <Ctx value={{ me, signedOut: () => {
      clearWebhook()
      setMe({ state: 'out' })
    } }}>{children}</Ctx>
}

export const useMe = () => useContext(Ctx)
