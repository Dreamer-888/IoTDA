import { Euler, MathUtils, Matrix4, Quaternion } from 'three';

// This is the six-face CSS instrument supplied by Stitch, with live orientation.
// Source display convention: pitch -> X, roll -> Y, yaw -> Z (XYZ).
// Physical mounting and device-axis mapping still require the documented hardware check.
export const SENSOR_ALIGNMENT = { order: 'XYZ', pitchAxis: 'x', rollAxis: 'y', yawAxis: 'z' };
export function poseQuaternion(sample) {
  return new Quaternion().setFromEuler(new Euler(MathUtils.degToRad(sample.pitch), MathUtils.degToRad(sample.roll), MathUtils.degToRad(sample.yaw), SENSOR_ALIGNMENT.order));
}
export class SensorScene {
  constructor(container) {
    this.container = container;
    this.model = container.querySelector('#imu-cube');
    if (!this.model) throw new Error('Sensor model missing');
    this.grid = container.querySelector('#scene-grid');
    this.viewAngles = document.getElementById('view-angles');
    this.pose = new Quaternion(); this.target = new Quaternion();
    this.view = { x: 0, y: 0 }; this.scale = 1;
    this.active = false; this.hasSample = false; this.gridVisible = true; this.orthographic = false;
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.pointer = null;
    container.addEventListener('pointerdown', (e) => {
      if (!this.hasSample || e.button !== 0) return;
      this.pointer = { id: e.pointerId, x: e.clientX, y: e.clientY };
      container.setPointerCapture(e.pointerId);
      container.focus({ preventScroll: true });
    });
    container.addEventListener('pointermove', (e) => {
      if (!this.pointer || e.pointerId !== this.pointer.id) return;
      this.view.y += (e.clientX - this.pointer.x) * .5;
      this.view.x -= (e.clientY - this.pointer.y) * .5;
      this.pointer.x = e.clientX; this.pointer.y = e.clientY;
      this.draw();
    });
    const release = () => { this.pointer = null; };
    for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) container.addEventListener(event, release);
    container.addEventListener('wheel', (e) => {
      if (!this.hasSample) return;
      e.preventDefault(); this.zoom(Math.exp(-e.deltaY * .001));
    }, { passive: false });
    container.addEventListener('keydown', (e) => {
      if (!this.hasSample || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','0','Home'].includes(e.key)) return;
      e.preventDefault();
      if (e.key === 'ArrowLeft') this.view.y -= 8;
      if (e.key === 'ArrowRight') this.view.y += 8;
      if (e.key === 'ArrowUp') this.view.x -= 8;
      if (e.key === 'ArrowDown') this.view.x += 8;
      if (e.key === '+' || e.key === '=') this.zoom(1.1);
      if (e.key === '-') this.zoom(1 / 1.1);
      if (e.key === '0' || e.key === 'Home') this.reset();
      this.draw();
    });
    this.tick = () => {
      this.frame = null;
      if (!this.active || !this.hasSample) return;
      this.pose.slerp(this.target, this.reduced ? 1 : .16);
      this.draw();
      if (this.pose.angleTo(this.target) > .0001) this.frame = requestAnimationFrame(this.tick);
    };
  }
  update(sample) {
    const valid = sample && ['pitch','roll','yaw'].every((key) => Number.isFinite(sample[key]));
    const first = !this.hasSample;
    this.hasSample = !!valid;
    this.model.style.visibility = valid ? 'visible' : 'hidden';
    if (!valid) { cancelAnimationFrame(this.frame); this.frame = null; this.viewAngles.textContent = '显示姿态: —'; return; }
    this.target.copy(poseQuaternion(sample));
    if (first) this.pose.copy(this.target);
    this.draw();
    if (this.active && !this.frame) this.frame = requestAnimationFrame(this.tick);
  }
  draw() {
    if (!this.hasSample) return;
    const camera = new Quaternion().setFromEuler(new Euler(MathUtils.degToRad(this.view.x), MathUtils.degToRad(this.view.y), 0, 'XYZ'));
    const orientation = camera.multiply(this.pose);
    const matrix = new Matrix4().makeRotationFromQuaternion(orientation);
    this.model.style.transform = 'scale(' + this.scale + ') matrix3d(' + matrix.elements.join(',') + ')';
    const angles = new Euler().setFromQuaternion(orientation, 'XYZ');
    const degrees = (n) => (n >= 0 ? '+' : '') + MathUtils.radToDeg(n).toFixed(1) + '°';
    this.viewAngles.textContent = `显示姿态: Pitch: ${degrees(angles.x)} / Roll: ${degrees(angles.y)} / Yaw: ${degrees(angles.z)}`;
  }
  reset() { this.view = { x: 0, y: 0 }; this.scale = 1; this.draw(); }
  zoom(factor) { this.scale = MathUtils.clamp(this.scale * factor, .65, 1.8); this.draw(); }
  toggleGrid() { this.gridVisible = !this.gridVisible; this.grid.style.display = this.gridVisible ? '' : 'none'; return this.gridVisible; }
  toggleProjection() { this.orthographic = !this.orthographic; this.container.style.perspective = this.orthographic ? 'none' : '900px'; return this.orthographic; }
  setActive(active) {
    this.active = active;
    if (!active) { cancelAnimationFrame(this.frame); this.frame = null; }
    else if (this.hasSample && !this.frame) this.frame = requestAnimationFrame(this.tick);
  }
}
