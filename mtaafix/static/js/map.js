// Leaflet map with OpenStreetMap tiles.
//   data-mode="pick": the resident taps the map to place (or drag) a pin; its coordinates
//                     are written into the hidden latitude/longitude inputs (REQ-6).
//   data-mode="view": shows one report's location.
(function () {
  const el = document.getElementById("map");
  if (!el || typeof L === "undefined") { return; }

  const mode = el.dataset.mode;
  const lat = parseFloat(el.dataset.lat);
  const lng = parseFloat(el.dataset.lng);
  const zoom = parseInt(el.dataset.zoom, 10) || 13;

  const map = L.map(el).setView([lat, lng], zoom);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  if (mode === "view") {
    L.marker([lat, lng]).addTo(map);
    return;
  }

  // Pick mode
  const latInput = document.getElementById(el.dataset.latInput);
  const lngInput = document.getElementById(el.dataset.lngInput);
  const status = document.getElementById(el.dataset.status || "");
  const locate = document.getElementById("locate-me");
  let marker = null;

  function setPin(latlng) {
    if (marker) {
      marker.setLatLng(latlng);
    } else {
      marker = L.marker(latlng, { draggable: true, keyboard: true, title: "Problem location" }).addTo(map);
      marker.on("dragend", () => setPin(marker.getLatLng()));
    }
    latInput.value = latlng.lat.toFixed(6);
    lngInput.value = latlng.lng.toFixed(6);
    el.classList.remove("is-invalid");
    if (status) {
      status.textContent = `Pin placed at ${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}`;
      status.classList.add("is-set");
    }
  }

  // "Use my location": only shown when the browser can do it (needs HTTPS on a real site).
  if (locate && "geolocation" in navigator && window.isSecureContext) {
    locate.hidden = false;
    locate.addEventListener("click", () => {
      const label = locate.innerHTML;
      locate.disabled = true;
      locate.textContent = "Finding you…";
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const here = L.latLng(pos.coords.latitude, pos.coords.longitude);
          setPin(here);
          map.setView(here, 17);
          locate.disabled = false;
          locate.innerHTML = label;
        },
        () => {
          if (status) status.textContent = "Could not get your location. Tap the map instead.";
          locate.disabled = false;
          locate.innerHTML = label;
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
      );
    });
  }

  // If the form came back with an error, put the pin where the resident left it.
  if (latInput.value && lngInput.value) {
    const saved = L.latLng(parseFloat(latInput.value), parseFloat(lngInput.value));
    setPin(saved);
    map.setView(saved, 16);
  }

  map.on("click", (event) => setPin(event.latlng));
})();
