import { createContext, useContext, useEffect, useState } from 'react'
import { getMe, type Me } from './api'

export type MeState = { state: 'loading' } | { state: 'out' } | { state: 'in'; me: Me }

const Ctx = createContext<{ me: MeState; signedOut: () => void }>({ me: { state: 'loading' }, signedOut: () => {} })

export function MeProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<MeState>({ state: 'loading' })
  useEffect(() => {
    const ctl = new AbortController()
    getMe(ctl.signal).then(
      (m) => setMe(m ? { state: 'in', me: m } : { state: 'out' }),
      // The API being down is not a reason to hide the whole site: treat it as signed out.
      () => !ctl.signal.aborted && setMe({ state: 'out' }),
    )
    return () => ctl.abort()
  }, [])
  return <Ctx value={{ me, signedOut: () => setMe({ state: 'out' }) }}>{children}</Ctx>
}

export const useMe = () => useContext(Ctx)
