// Twilight water + citrus flare. Draft palette for a warmer, brighter branch of the system.
// Slots are the future-facing theme contract; palette keeps legacy semantic tokens alive.
export const slots = {
  c1Light: "#6fe8ff",
  c1Med: "#159ca0",
  c1Dark: "#123a5d",
  c2Light: "#f5efde",
  c2Med: "#ff8a1f",
  c2Dark: "#bb450a",
  veryLight: "#fff9ef",
  veryDark: "#07111f",
};

export const palette = {
  white: slots.veryLight,
  softWhite: "#fff4df",
  mintGreen: slots.c1Light,
  pearGreen: slots.c2Med,
  black: slots.veryDark,
  purple: slots.c1Med,
  deepPurple: slots.c1Dark,
  indigo: slots.veryDark,
  lavender: slots.c2Light,
  lavenderDark: "#f6d7a4",
  petrolBlue: slots.veryDark,
  violetGlow: "#b9f3ff",
};

const lightLayers = [
  `radial-gradient(1200px 620px at 16% 10%, color-mix(in oklab, ${slots.c1Light} 26%, transparent), transparent 62%)`,
  `radial-gradient(980px 540px at 84% 18%, color-mix(in oklab, ${slots.c2Light} 34%, transparent), transparent 66%)`,
  `radial-gradient(900px 500px at 50% 84%, color-mix(in oklab, ${slots.c2Med} 14%, transparent), transparent 72%)`,
  `linear-gradient(145deg, color-mix(in oklab, ${slots.veryLight} 86%, ${slots.c2Light} 14%), color-mix(in oklab, ${slots.c1Light} 14%, ${slots.veryLight} 86%))`,
];

const darkLayers = [
  `radial-gradient(1520px 800px at -8% 30%, color-mix(in oklab, ${slots.c1Dark} 60%, rgba(0, 0, 0, 0.58)), transparent 72%)`,
  `radial-gradient(1460px 760px at 110% 20%, color-mix(in oklab, ${slots.c1Med} 44%, rgba(10, 60, 70, 0.5)), transparent 74%)`,
  `radial-gradient(1200px 780px at 42% 118%, color-mix(in oklab, ${slots.c2Med} 40%, rgba(0, 0, 0, 0.42)), transparent 77%)`,
  `radial-gradient(940px 620px at -4% 116%, color-mix(in oklab, ${slots.c2Dark} 48%, rgba(0, 0, 0, 0.52)), transparent 84%)`,
  `radial-gradient(860px 520px at 8% -4%, rgba(255, 247, 230, 0.08), transparent 80%)`,
  `radial-gradient(620px 420px at 96% 98%, rgba(182, 244, 255, 0.12), transparent 86%)`,
  `linear-gradient(126deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0) 34%, rgba(255, 255, 255, 0.05) 72%, rgba(255, 255, 255, 0.02) 96%)`,
  `linear-gradient(210deg, rgba(7, 17, 31, 0.99), rgba(7, 12, 22, 0.96))`,
];

export const gradients = {
  light: lightLayers.join(", "),
  dark: darkLayers.join(", "),
};

export default { slots, palette, gradients };
