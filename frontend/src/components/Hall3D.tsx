import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Seat } from "../api";
import { layoutHall, viewFrom } from "./hallLayout";
import { buildHallScene, type HallScene } from "./hallScene";
import SeatView, { seatGeometry } from "./SeatView";

type Props = { seats: Seat[]; seat: Seat; title: string; selectedIds?: Set<number>; onClose: () => void };
type Mode = "seat" | "overview";

/** A 3D model of the hall built from our own seat layout. "From my seat": look around from
 *  the seat's real position and height. "Hall overview": orbit the whole hall and tap any seat
 *  to sit there. Loaded on demand (it is a big library), and falls back to the picture view on
 *  a device without WebGL. */
export default function Hall3D({ seats, seat, title, selectedIds, onClose }: Props) {
  // The seat page refreshes its seat list every few seconds. The 3D view uses the list as it was when
  // it was opened, so a refresh never rebuilds the scene or throws the viewer's camera back.
  const initial = useRef({ seats, selectedIds });
  const layout = useMemo(() => layoutHall(initial.current.seats), []);
  const [currentId, setCurrentId] = useState(seat.id);
  const [mode, setMode] = useState<Mode>("seat");
  const [webgl, setWebgl] = useState(true);
  const host = useRef<HTMLDivElement>(null);
  const three = useRef<{
    renderer: THREE.WebGLRenderer; camera: THREE.PerspectiveCamera; controls: OrbitControls; hall: HallScene;
    render: () => void; apply: () => void;
  } | null>(null);
  // refs the effects read, so changing seat/mode does not rebuild the whole scene
  const live = useRef({ currentId, mode });
  live.current = { currentId, mode };
  const look = useRef({ yaw: 0, pitch: 0, baseX: 0, baseY: 0 });

  // build the scene once per hall
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch { setWebgl(false); return; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(el.clientWidth, el.clientHeight);
    el.appendChild(renderer.domElement);
    renderer.domElement.style.touchAction = "none";

    const hall = buildHallScene(layout, { title, selectedIds: initial.current.selectedIds, chosenId: null });
    const camera = new THREE.PerspectiveCamera(58, el.clientWidth / el.clientHeight, 0.1, 200);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.maxPolarAngle = Math.PI * 0.49;
    controls.minDistance = 3; controls.maxDistance = 60;
    const render = () => renderer.render(hall.scene, camera);
    controls.addEventListener("change", render);

    const apply = () => {
      const { currentId: id, mode: md } = live.current;
      const p = layout.seats.find((s) => s.seat.id === id);
      if (!p) return;
      camera.fov = el.clientWidth < el.clientHeight ? 78 : 58;            // a narrower phone screen needs a wider lens
      camera.updateProjectionMatrix();
      if (md === "seat") {
        controls.enabled = false;
        const v = viewFrom(layout, p);
        camera.position.set(...v.position);
        camera.rotation.order = "YXZ";
        camera.lookAt(...v.target);
        look.current = { yaw: 0, pitch: 0, baseX: camera.rotation.x, baseY: camera.rotation.y };
        hall.setChosen(null);
      } else {
        camera.rotation.order = "XYZ";
        controls.enabled = true;
        camera.position.set(layout.width * 0.9, layout.backFloorY + 9, layout.depth + 7);
        controls.target.set(0, 1.5, layout.depth * 0.45);
        controls.update();
        hall.setChosen(id);
      }
      render();
    };
    three.current = { renderer, camera, controls, hall, render, apply };
    apply();

    // look around (seat view) by dragging; tap a seat (overview) to sit there
    const raycaster = new THREE.Raycaster();
    let down: { x: number; y: number; moved: number } | null = null;
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY, moved: 0 }; };
    const onMove = (e: PointerEvent) => {
      if (!down) return;
      const dx = e.clientX - down.x, dy = e.clientY - down.y;
      down.moved += Math.abs(dx) + Math.abs(dy);
      down.x = e.clientX; down.y = e.clientY;
      if (live.current.mode === "seat") {
        const l = look.current;
        l.yaw = Math.max(-1.1, Math.min(1.1, l.yaw + dx * 0.005));
        l.pitch = Math.max(-0.5, Math.min(0.5, l.pitch + dy * 0.005));
        camera.rotation.y = l.baseY + l.yaw;
        camera.rotation.x = l.baseX + l.pitch;
        render();
      }
    };
    const onUp = (e: PointerEvent) => {
      const wasTap = down !== null && down.moved < 6;
      down = null;
      if (!wasTap || live.current.mode !== "overview") return;
      const r = renderer.domElement.getBoundingClientRect();
      raycaster.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
      const hit = raycaster.intersectObject(hall.seatBases)[0];
      if (hit && hit.instanceId !== undefined) {
        setCurrentId(hall.seatIds[hit.instanceId]);
        setMode("seat");
      }
    };
    const canvas = renderer.domElement;
    canvas.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);

    const ro = new ResizeObserver(() => {
      renderer.setSize(el.clientWidth, el.clientHeight);
      camera.aspect = el.clientWidth / el.clientHeight;
      apply();
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      controls.dispose(); hall.dispose(); renderer.dispose();
      canvas.remove();
      three.current = null;
    };
  }, [layout, title]);

  // moving between seats / modes only repositions the camera
  useEffect(() => { three.current?.apply(); }, [currentId, mode]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const placed = layout.seats.find((s) => s.seat.id === currentId) ?? layout.seats[0];
  const stats = placed ? seatGeometry(placed.rowIdx, layout.nRows, placed.colIdx, layout.nCols) : null;

  if (!webgl) {
    return (
      <div className="hall3d" role="dialog" aria-label="Seat view">
        <div className="hall3d-fallback">
          <p className="muted">3D isn't available on this device, so here is the picture view instead.</p>
          <SeatView seats={seats} seat={seat} title={title} onClose={onClose} />
        </div>
      </div>
    );
  }
  return (
    <div className="hall3d" role="dialog" aria-label="3D hall">
      <div className="hall3d-canvas" ref={host} />
      <div className="hall3d-top">
        <div>
          <strong>Seat {placed?.seat.label}</strong>
          <span className="muted"> {placed?.seat.seat_type}</span>
        </div>
        <div className="hall3d-modes">
          <button className={mode === "seat" ? "primary" : ""} onClick={() => setMode("seat")}>From my seat</button>
          <button className={mode === "overview" ? "primary" : ""} onClick={() => setMode("overview")}>Hall overview</button>
        </div>
        <button onClick={onClose} aria-label="Close 3D view">✕</button>
      </div>
      <div className="hall3d-bottom">
        {stats && (
          <div className={`seatview-note ${stats.tone}`}>
            {stats.verdict}
            <span className="muted small"> - {stats.rowsFromScreen + 1} rows from the screen, {Math.round(stats.screenAngle)}° wide{stats.offAxis > 1 ? `, ${Math.round(stats.offAxis)}° off-centre` : ", dead centre"}</span>
          </div>
        )}
        <div className="muted small">{mode === "seat" ? "Drag to look around." : "Drag to turn the hall, scroll or pinch to zoom, tap any seat to sit there."} Built from the hall's layout, not a photo.</div>
      </div>
    </div>
  );
}
