import { Link, Route, Routes } from 'react-router'
import { NotFound } from './components/State'
import BuildDiff from './pages/BuildDiff'
import Timeline from './pages/Timeline'

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
        <span className="tagline">What each patch changed in the game data</span>
      </header>
      <main id="main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Timeline />} />
          <Route path="/builds/:label" element={<BuildDiff />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </>
  )
}
