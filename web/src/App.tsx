import { useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router'
import { NotFound } from './components/State'
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

export default function App() {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="masthead">
        <Link to="/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            &#9638;
          </span>
          Wardogs Patch Tracker
        </Link>
        <nav className="mainnav" aria-label="Main">
          <NavLink to="/" end>
            Builds
          </NavLink>
          <NavLink to="/changes">Changes</NavLink>
        </nav>
        <HeaderSearch />
      </header>
      <main id="main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Timeline />} />
          <Route path="/builds/:label" element={<BuildDiff />} />
          <Route path="/search" element={<Search />} />
          <Route path="/asset" element={<AssetHistory />} />
          <Route path="/changes" element={<Changes />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </>
  )
}
