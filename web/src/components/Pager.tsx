export default function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  if (pages <= 1) return null
  return (
    <nav className="pager" aria-label="Pagination">
      <button type="button" className="btn" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        &larr; Previous
      </button>
      <span>
        Page {page} of {pages}
      </span>
      <button type="button" className="btn" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next &rarr;
      </button>
    </nav>
  )
}
