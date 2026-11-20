import * as THREE from "three";
import { HALL, type HallLayout } from "./hallLayout";

export type HallScene = {
  scene: THREE.Scene;
  seatBases: THREE.InstancedMesh;
  /** seat id for each instance of seatBases (so a click on the 3D seat can be turned into a seat) */
  seatIds: number[];
  setChosen: (seatId: number | null) => void;
  dispose: () => void;
};

const COLORS = {
  available: 0x3b7a57, selected: 0x2ecc71, mine: 0x3b6cf5, held: 0xd6932a, booked: 0x555a70, chosen: 0xff4d7d,
};
const colorOf = (status: string, selected: boolean) =>
  selected ? COLORS.selected : status === "held_by_me" ? COLORS.mine : status === "held" ? COLORS.held
    : status === "booked" ? COLORS.booked : COLORS.available;

/** The screen picture: the movie title on a bright gradient (a plain colour where canvas is unavailable, e.g. in tests). */
function screenTexture(title: string): THREE.Texture | null {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = 1024; canvas.height = 448;
  const g = canvas.getContext("2d");
  if (!g) return null;
  const grad = g.createLinearGradient(0, 0, canvas.width, canvas.height);
  grad.addColorStop(0, "#1c2b66"); grad.addColorStop(0.5, "#4a6fd8"); grad.addColorStop(1, "#16204a");
  g.fillStyle = grad; g.fillRect(0, 0, canvas.width, canvas.height);
  g.fillStyle = "#ffffff"; g.font = "bold 84px Georgia, serif"; g.textAlign = "center"; g.textBaseline = "middle";
  g.fillText(title.length > 26 ? title.slice(0, 25) + "…" : title, canvas.width / 2, canvas.height / 2);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

export function buildHallScene(layout: HallLayout, opts: { title: string; selectedIds?: Set<number>; chosenId?: number | null }): HallScene {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x07080f);
  const disposables: { dispose: () => void }[] = [];
  const keep = <T extends { dispose: () => void }>(o: T): T => { disposables.push(o); return o; };

  const { width, depth, screen, backFloorY } = layout;
  const roomW = width + 4, roomH = backFloorY + 7, roomD = depth + 3;

  // room: dark walls, ceiling
  const wallMat = keep(new THREE.MeshLambertMaterial({ color: 0x14162a, side: THREE.BackSide }));
  const room = new THREE.Mesh(keep(new THREE.BoxGeometry(roomW, roomH, roomD)), wallMat);
  room.position.set(0, roomH / 2 - 0.2, roomD / 2 - 0.5);
  scene.add(room);

  // stepped floor: one slab per row
  const floorMat = keep(new THREE.MeshLambertMaterial({ color: 0x22253d }));
  const slabGeo = keep(new THREE.BoxGeometry(roomW - 0.2, 1, HALL.rowDepth));
  for (let r = 0; r < layout.nRows; r++) {
    const rowsFromScreen = layout.nRows - 1 - r;
    const slab = new THREE.Mesh(slabGeo, floorMat);
    slab.scale.y = 1;
    slab.position.set(0, rowsFromScreen * HALL.rise - 0.5, HALL.screenGap + rowsFromScreen * HALL.rowDepth);
    scene.add(slab);
  }
  // the floor in front of the first row, up to the screen wall
  const apron = new THREE.Mesh(keep(new THREE.BoxGeometry(roomW - 0.2, 1, HALL.screenGap)), floorMat);
  apron.position.set(0, -0.5, HALL.screenGap / 2);
  scene.add(apron);

  // the screen, with a soft glow of light in front of it
  const tex = screenTexture(opts.title);
  if (tex) keep(tex);
  const screenMat = keep(new THREE.MeshBasicMaterial(tex ? { map: tex } : { color: 0x4a6fd8 }));
  const screenMesh = new THREE.Mesh(keep(new THREE.PlaneGeometry(screen.w, screen.h)), screenMat);
  screenMesh.position.set(0, screen.cy, 0.02);
  scene.add(screenMesh);
  const glow = new THREE.PointLight(0x7aa2ff, 55, 60, 1.4);
  glow.position.set(0, screen.cy, 1.5);
  scene.add(glow);
  scene.add(new THREE.AmbientLight(0x6b72a8, 0.9));

  // seats: cushion + backrest, coloured by status (instanced: hundreds of seats, two draw calls)
  const n = layout.seats.length;
  const baseGeo = keep(new THREE.BoxGeometry(0.44, 0.14, 0.44));
  const backGeo = keep(new THREE.BoxGeometry(0.44, 0.42, 0.08));
  const seatMat = keep(new THREE.MeshLambertMaterial({ color: 0xffffff }));
  const seatBases = new THREE.InstancedMesh(baseGeo, seatMat, n);
  const seatBacks = new THREE.InstancedMesh(backGeo, seatMat, n);
  const headGeo = keep(new THREE.SphereGeometry(0.115, 10, 8));
  const bodyGeo = keep(new THREE.BoxGeometry(0.34, 0.42, 0.18));
  const personMat = keep(new THREE.MeshLambertMaterial({ color: 0x0b0c16 }));
  const taken = layout.seats.filter((p) => p.seat.status === "booked" || p.seat.status === "held");
  const heads = new THREE.InstancedMesh(headGeo, personMat, taken.length);
  const bodies = new THREE.InstancedMesh(bodyGeo, personMat, taken.length);

  const m = new THREE.Matrix4(), color = new THREE.Color();
  const seatIds: number[] = [];
  const indexOfSeat = new Map<number, number>();
  const selected = opts.selectedIds ?? new Set<number>();
  layout.seats.forEach((p, i) => {
    seatIds.push(p.seat.id); indexOfSeat.set(p.seat.id, i);
    m.makeTranslation(p.x, p.y + HALL.seatHeight, p.z);
    seatBases.setMatrixAt(i, m);
    m.makeTranslation(p.x, p.y + HALL.seatHeight + 0.28, p.z + 0.22);   // backrest is BEHIND the cushion (further from the screen)
    seatBacks.setMatrixAt(i, m);
    color.setHex(colorOf(p.seat.status, selected.has(p.seat.id)));
    seatBases.setColorAt(i, color); seatBacks.setColorAt(i, color);
  });
  taken.forEach((p, i) => {                                                   // a person sitting in every taken seat
    m.makeTranslation(p.x, p.y + HALL.seatHeight + 0.62, p.z + 0.08); heads.setMatrixAt(i, m);
    m.makeTranslation(p.x, p.y + HALL.seatHeight + 0.3, p.z + 0.14); bodies.setMatrixAt(i, m);
  });
  [seatBases, seatBacks, heads, bodies].forEach((mesh) => { mesh.instanceMatrix.needsUpdate = true; scene.add(mesh); });
  if (seatBases.instanceColor) seatBases.instanceColor.needsUpdate = true;
  if (seatBacks.instanceColor) seatBacks.instanceColor.needsUpdate = true;

  // the seat being looked from glows (a small pink pillar of light above it, visible from the overview)
  const marker = new THREE.Mesh(keep(new THREE.CylinderGeometry(0.05, 0.05, 1.6, 8)), keep(new THREE.MeshBasicMaterial({ color: COLORS.chosen })));
  marker.visible = false;
  scene.add(marker);
  const setChosen = (seatId: number | null) => {
    const idx = seatId === null ? undefined : indexOfSeat.get(seatId);
    layout.seats.forEach((p, i) => {
      color.setHex(i === idx ? COLORS.chosen : colorOf(p.seat.status, selected.has(p.seat.id)));
      seatBases.setColorAt(i, color); seatBacks.setColorAt(i, color);
    });
    if (seatBases.instanceColor) seatBases.instanceColor.needsUpdate = true;
    if (seatBacks.instanceColor) seatBacks.instanceColor.needsUpdate = true;
    if (idx === undefined) { marker.visible = false; return; }
    const p = layout.seats[idx];
    marker.position.set(p.x, p.y + HALL.seatHeight + 1.4, p.z);
    marker.visible = true;
  };
  setChosen(opts.chosenId ?? null);

  const dispose = () => {
    [seatBases, seatBacks, heads, bodies].forEach((mesh) => mesh.dispose());
    disposables.forEach((d) => d.dispose());
  };
  return { scene, seatBases, seatIds, setChosen, dispose };
}
