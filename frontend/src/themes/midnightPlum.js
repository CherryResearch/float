// Midnight plum + berry palette. Draft palette for a more moody, elegant branch of the system.
// Slots are the future-facing theme contract; palette keeps legacy semantic tokens alive.
export const slots = {
  c1Light: "#ecd9ff",
  c1Med: "#904ac9",
  c1Dark: "#17032a",
  c2Light: "#f0e9df",
  c2Med: "#dc5414",
  c2Dark: "#3d0727",
  veryLight: "#fff7fb",
  veryDark: "#100714",
};

export const palette = {
  white: slots.veryLight,
  softWhite: "#fff0f7",
  mintGreen: slots.c1Light,
  pearGreen: slots.c2Med,
  black: slots.veryDark,
  purple: slots.c1Med,
  deepPurple: slots.c1Dark,
  indigo: slots.veryDark,
  lavender: slots.c1Light,
  lavenderDark: "#dbb8f2",
  petrolBlue: slots.veryDark,
  violetGlow: "#f4bdd3",
};

const lightLayers = [
  `radial-gradient(1200px 620px at 18% 10%, color-mix(in oklab, ${slots.c1Light} 30%, transparent), transparent 62%)`,
  `radial-gradient(980px 540px at 84% 18%, color-mix(in oklab, ${slots.c2Light} 34%, transparent), transparent 66%)`,
  `radial-gradient(900px 500px at 50% 84%, color-mix(in oklab, ${slots.c2Med} 14%, transparent), transparent 72%)`,
  `linear-gradient(145deg, color-mix(in oklab, ${slots.veryLight} 86%, ${slots.c1Light} 14%), color-mix(in oklab, ${slots.c2Light} 16%, ${slots.veryLight} 84%))`,
];

const darkLayers = [
  `radial-gradient(1520px 800px at -8% 30%, color-mix(in oklab, ${slots.c1Dark} 58%, rgba(0, 0, 0, 0.6)), transparent 72%)`,
  `radial-gradient(1460px 760px at 110% 20%, color-mix(in oklab, ${slots.c1Med} 42%, rgba(52, 14, 61, 0.54)), transparent 74%)`,
  `radial-gradient(1200px 780px at 42% 118%, color-mix(in oklab, ${slots.c2Med} 40%, rgba(0, 0, 0, 0.44)), transparent 77%)`,
  `radial-gradient(940px 620px at -4% 116%, color-mix(in oklab, ${slots.c2Dark} 48%, rgba(0, 0, 0, 0.54)), transparent 84%)`,
  `radial-gradient(860px 520px at 8% -4%, rgba(252, 235, 247, 0.08), transparent 80%)`,
  `radial-gradient(620px 420px at 96% 98%, rgba(244, 189, 211, 0.12), transparent 86%)`,
  `linear-gradient(126deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0) 34%, rgba(255, 255, 255, 0.05) 72%, rgba(255, 255, 255, 0.02) 96%)`,
  `linear-gradient(210deg, rgba(16, 7, 20, 0.99), rgba(11, 5, 15, 0.96))`,
];

export const gradients = {
  light: lightLayers.join(", "),
  dark: darkLayers.join(", "),
};

export default { slots, palette, gradients };
