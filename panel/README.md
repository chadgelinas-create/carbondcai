# Skid 2 Frontend

The development build shows the Simulator Lab. It uses simulated devices and does not connect to
physical equipment. The production build keeps simulation disabled.

The supported container-based checks run from the repository root:

```bash
make panel-check
docker build --target production --tag skid2-panel:local panel
```

## Manual Simulator Lab check

1. Run `make up` and open <http://127.0.0.1:5173>.
2. Confirm **Devices** lists seven logical simulators.
3. Select **Next step** once so point-based fault choices have a normal reading.
4. Open **Fault testing**, choose a device and a plain-language problem, then review the expected
   result before selecting **Start test**.
5. Run the next step and confirm the device values, status, test ID, and **Activity** agree.
6. Select **Clear test**, run the next step, and confirm recovery is recorded.
7. Try a timed test and a device fault; confirm the device fault waits for manual clearing.
8. Select **Download report** and confirm the JSON contains the same test IDs and limitations.
9. Select **Repeat steps** and confirm the displayed result does not change.

Fault controls are intentionally separate from normal value forms. The production build does not
show the Simulator Lab, and the backend rejects every simulation and fault-test route when
simulation is disabled.

With supported Node 24 LTS and npm 11/12 installed natively:

```bash
npm ci
npm run dev
npm run check
```

All API calls belong in `src/api/`. Browser runtime configuration is read from `public/config.js`; it may contain public endpoint values but never secrets.
