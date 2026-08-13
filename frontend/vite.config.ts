import vinext from "vinext";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vinext()],
  server: {
    allowedHosts: ["orientia-tsutsugamushi-typing.org"],
  },
});
