// Keep in sync with CSS variables defined in styles/theme.css.
// Slots are the future-facing theme contract; palette keeps legacy semantic tokens alive.
export const slots = {
  c1Light: "#e4d9f3",
  c1Med: "#630ac3",
  c1Dark: "#340865",
  c2Light: "#86eaa0",
  c2Med: "#21b228",
  c2Dark: "#166d2a",
  veryLight: "#ffffff",
  veryDark: "#090d17",
};

export const palette = {
  white: slots.veryLight,
  softWhite: "#f2eef7",
  mintGreen: slots.c2Light,
  pearGreen: slots.c2Med,
  black: slots.veryDark,
  purple: slots.c1Med,
  deepPurple: slots.c1Dark,
  indigo: "#390892",
  lavender: slots.c1Light,
  lavenderDark: "#b29ed9",
  petrolBlue: "#0b1120",
  violetGlow: "#c3aceb",
};

const lightLayers = [
  `radial-gradient(1200px 600px at 20% 10%, color-mix(in oklab, ${slots.c1Light} 18%, transparent), transparent 60%)`,
  `radial-gradient(900px 500px at 85% 20%, color-mix(in oklab, ${slots.c2Light} 18%, transparent), transparent 60%)`,
  `radial-gradient(800px 400px at 40% 85%, color-mix(in oklab, ${slots.c1Light} 20%, transparent), transparent 70%)`,
  `linear-gradient(145deg, color-mix(in oklab, ${slots.c1Light} 72%, ${slots.veryLight} 28%), color-mix(in oklab, ${palette.softWhite} 90%, #fff4f8 10%))`,
];

const darkLayers = [
  `radial-gradient(1560px 840px at -10% 18%, color-mix(in oklab, ${slots.c1Light} 14%, rgba(9, 13, 23, 0.96)), transparent 72%)`,
  `radial-gradient(1480px 780px at 108% 16%, color-mix(in oklab, ${slots.c1Med} 42%, rgba(14, 10, 34, 0.64)), transparent 76%)`,
  `radial-gradient(1180px 760px at 46% 118%, color-mix(in oklab, ${palette.indigo} 22%, rgba(6, 9, 18, 0.92)), transparent 82%)`,
  `radial-gradient(760px 420px at 100% 100%, color-mix(in oklab, ${slots.c2Med} 14%, rgba(7, 10, 18, 0.9)), transparent 86%)`,
  `linear-gradient(126deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0) 38%, rgba(255, 255, 255, 0.03) 88%)`,
  `linear-gradient(210deg, rgba(7, 10, 18, 0.99), rgba(10, 7, 20, 0.96))`,
];

export const gradients = {
  light: lightLayers.join(", "),
  dark: darkLayers.join(", "),
};

export default { slots, palette, gradients };
