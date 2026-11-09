import type { Movie } from "../api";

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

export function wrapTitle(title: string, max = 12): string[] {
  const lines: string[] = [];
  let cur = "";
  for (const w of title.split(/\s+/)) {
    if (cur && (cur + " " + w).length > max) { lines.push(cur); cur = w; }
    else cur = cur ? cur + " " + w : w;
  }
  if (cur) lines.push(cur);
  return lines.slice(0, 4);
}

/** The movie's poster image, or - when none was uploaded - original generated
 *  art (a gradient + title) so every card has a poster without using anyone's
 *  copyrighted artwork. */
export default function Poster({ movie, className = "" }: { movie: Pick<Movie, "title" | "genre" | "poster_url">; className?: string }) {
  if (movie.poster_url) return <img className={`poster ${className}`} src={movie.poster_url} alt={`${movie.title} poster`} loading="lazy" />;
  const h = hash(movie.title);
  const h1 = h % 360, h2 = (h1 + 40 + (h >> 8) % 80) % 360;
  const lines = wrapTitle(movie.title);
  const cx = 60 + (h % 180), cy = 90 + ((h >> 4) % 80);
  return (
    <svg className={`poster ${className}`} viewBox="0 0 300 450" role="img" aria-label={`${movie.title} poster`} preserveAspectRatio="xMidYMid slice">
      <defs>
        <linearGradient id={`g${h}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={`hsl(${h1} 70% 28%)`} /><stop offset="1" stopColor={`hsl(${h2} 75% 12%)`} />
        </linearGradient>
        <radialGradient id={`r${h}`} cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor={`hsl(${h2} 90% 65%)`} stopOpacity=".75" /><stop offset="1" stopColor={`hsl(${h2} 90% 65%)`} stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="300" height="450" fill={`url(#g${h})`} />
      <circle cx={cx} cy={cy} r="130" fill={`url(#r${h})`} />
      <path d={`M0 ${330 + (h % 40)} Q 90 ${280 + (h % 50)} 170 ${340} T 300 ${300 + (h % 40)} V450 H0 Z`} fill="#000" opacity=".35" />
      <text x="150" y={250 - (lines.length - 1) * 22} textAnchor="middle" fill="#fff" fontFamily="Georgia, serif" fontWeight="700" fontSize="40">
        {lines.map((l, i) => <tspan key={i} x="150" dy={i === 0 ? 0 : 46}>{l.toUpperCase()}</tspan>)}
      </text>
      {movie.genre && <text x="150" y="415" textAnchor="middle" fill="#fff" opacity=".7" fontSize="14" letterSpacing="3">{movie.genre.split("/")[0].toUpperCase()}</text>}
    </svg>
  );
}
