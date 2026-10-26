import test from 'node:test';
import assert from 'node:assert/strict';
import { Vector3 } from 'three';
import { poseQuaternion } from '../lib/sensor-scene.js';

const closeVector = (actual, expected) => {
  assert.ok(actual.distanceTo(new Vector3(...expected)) < 1e-10,
    `Expected ${expected}, got ${actual.toArray()}`);
};

test('Stitch display convention preserves the neutral pose and the three positive axes', () => {
  const neutral = { pitch: 0, roll: 0, yaw: 0 };
  closeVector(new Vector3(1, 2, 3).applyQuaternion(poseQuaternion(neutral)), [1, 2, 3]);
  closeVector(new Vector3(0, 1, 0).applyQuaternion(poseQuaternion({ ...neutral, pitch: 90 })), [0, 0, 1]);
  closeVector(new Vector3(0, 0, 1).applyQuaternion(poseQuaternion({ ...neutral, roll: 90 })), [1, 0, 0]);
  closeVector(new Vector3(1, 0, 0).applyQuaternion(poseQuaternion({ ...neutral, yaw: 90 })), [0, 1, 0]);
});

test('interpolating across the yaw boundary takes the short path without a full turn', () => {
  const start = poseQuaternion({ pitch: 0, roll: 0, yaw: 179 });
  const end = poseQuaternion({ pitch: 0, roll: 0, yaw: -179 });
  closeVector(new Vector3(1, 0, 0).applyQuaternion(start.slerp(end, .5)), [-1, 0, 0]);
});
