// A white page with a slow, soft animated backdrop: drifting pastel colour
// blobs plus film-themed icons floating upward. Pure CSS animation (no JS loop),
// sits behind everything, ignores clicks, and stops for people who prefer
// reduced motion (see styles.css).
const ICONS = ["🎬", "🍿", "🎟️", "🎞️", "⭐", "🎥", "🍿", "🎟️", "🎬", "🎞️", "⭐", "🎥"];

export default function Background() {
  return (
    <div className="bg-anim" aria-hidden="true">
      <span className="blob b1" />
      <span className="blob b2" />
      <span className="blob b3" />
      <span className="blob b4" />
      {ICONS.map((icon, i) => (
        <span
          key={i}
          className="floater"
          style={{
            left: `${(i * 83) % 100}%`,
            fontSize: `${18 + ((i * 7) % 22)}px`,
            animationDuration: `${22 + ((i * 5) % 18)}s`,
            animationDelay: `-${(i * 3.7) % 22}s`,
          }}
        >
          {icon}
        </span>
      ))}
    </div>
  );
}
