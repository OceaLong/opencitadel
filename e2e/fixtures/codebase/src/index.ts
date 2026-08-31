export type Beacon = {
  name: string;
  rotationMinutes: number;
};

export function createBeacon(name: string, rotationMinutes: number): Beacon {
  return { name, rotationMinutes };
}

export function describeBeacon(beacon: Beacon): string {
  return `${beacon.name} rotates every ${beacon.rotationMinutes} minutes`;
}
