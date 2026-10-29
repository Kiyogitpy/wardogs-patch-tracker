import { useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router'
import { logout } from './api'
import { loginHref } from './account'
import { NotFound } from './components/State'
import { useMe } from './me'
import Account from './pages/Account'
import AssetHistory from './pages/AssetHistory'
import BuildDiff from './pages/BuildDiff'
import Changes from './pages/Changes'
import Search from './pages/Search'
import Timeline from './pages/Timeline'

function HeaderSearch() {
  const navigate = useNavigate()
  const { pathname, search } = useLocation()
  const urlQ = pathname === '/search' ? (new URLSearchParams(search).get('q') ?? '') : ''
  const [text, setText] = useState(urlQ)
  useEffect(() => setText(urlQ), [urlQ])
  return (
    <form
      className="hsearch"
      role="search"
      aria-label="Search assets"
      onSubmit={(e) => {
        e.preventDefault()
        const q = text.trim()
        navigate(q ? `/search?${new URLSearchParams({ q })}` : '/search')
      }}
    >
      <label>
        <span className="sr-only">Search assets by name or path</span>
        <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search assets" />
      </label>
      <button type="submit" className="btn">
        Search
      </button>
    </form>
  )
}

function AuthBox() {
  const { me, signedOut } = useMe()
  const { pathname, search } = useLocation()
  const [err, setErr] = useState('')
  if (me.state === 'loading') return <div className="who" aria-hidden="true" />
  if (me.state === 'out') {
    return (
      <div className="who">
        <a className="btn btn-primary btn-sm" href={loginHref(pathname + search)}>
          Sign in with Discord
        </a>
      </div>
    )
  }
  const { username, avatar_url } = me.me
  return (
    <div className="who">
      {avatar_url ? (
        <img className="avatar" src={avatar_url} alt="" width="28" height="28" />
      ) : (
        <span className="avatar" aria-hidden="true">
          {username.slice(0, 1).toUpperCase()}
        </span>
      )}
      <Link to="/account" className="who-name" title="Your watches">
        {username}
      </Link>
      <button
        type="button"
        className="linkbtn"
        onClick={async () => {
          try {
            await logout()
            signedOut()
          } catch (e) {
            setErr((e as Error).message)
          }
        }}
      >
        Sign out
      </button>
      <span className="sr-only" role="alert">
        {err && `Could not sign out. ${err}`}
      </span>
    </div>
  )
}

/** Dismissible notice for `?signin=failed`; dismissing removes the parameter. */
function SigninNotice() {
  const { pathname, search } = useLocation()
  const navigate = useNavigate()
  const sp = new URLSearchParams(search)
  if (sp.get('signin') !== 'failed') return null
  sp.delete('signin')
  const rest = sp.toString()
  return (
    <div className="wrap">
      <div className="state state-error notice" role="alert">
        <span className="state-glyph" aria-hidden="true">
          !
        </span>
        <p>
          <strong>Sign-in failed.</strong> Try again
        </p>
        <button type="button" className="btn" onClick={() => navigate(pathname + (rest ? `?${rest}` : ''), { replace: true })}>
          Dismiss
        </button>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="masthead">
        <div className="wrap masthead-in">
          <Link to="/" className="brand">
            <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
              <rect width="32" height="32" />
              <path d="M6 9h20M6 16h20M6 23h12" />
            </svg>
            <span className="brand-name">
              <b>Wardogs</b> patch tracker
            </span>
          </Link>
          <nav className="mainnav" aria-label="Main">
            <NavLink to="/" end>
              Builds
            </NavLink>
            <NavLink to="/changes">Changes</NavLink>
          </nav>
          <HeaderSearch />
          <AuthBox />
        </div>
      </header>
      <SigninNotice />
      <main id="main" tabIndex={-1} className="wrap">
        <Routes>
          <Route path="/" element={<Timeline />} />
          <Route path="/builds/:label" element={<BuildDiff />} />
          <Route path="/search" element={<Search />} />
          <Route path="/asset" element={<AssetHistory />} />
          <Route path="/changes" element={<Changes />} />
          <Route path="/account" element={<Account />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </>
  )
}
