// Type-ahead over Ember stops using /api/places. Loaded after landmarks.js (uses API).
// Each input only accepts a place picked from the list; free text is not a valid choice.

const selectedPlace = { origin: null, destination: null };   // {id, name, region, label}

function attachTypeahead(input, role) {
  const list = document.createElement("ul");
  list.className = "place-list";
  list.hidden = true;
  input.parentNode.insertBefore(list, input.nextSibling);

  let items = [];
  let active = -1;
  let timer = null;
  let lastQuery = "";

  function close() {
    list.hidden = true;
    active = -1;
  }

  function select(place) {
    selectedPlace[role] = place;
    input.value = place.label;
    input.classList.remove("invalid");
    close();
    // Picking a new origin invalidates a destination chosen before it
    if (role === "origin" && selectedPlace.destination) {
      selectedPlace.destination = null;
      const dest = document.getElementById("destination");
      dest.value = "";
      dest.placeholder = "Where are you headed?";
    }
  }

  function render() {
    list.replaceChildren();
    if (items.length === 0) {
      const li = document.createElement("li");
      li.className = "place-empty";
      li.textContent = role === "destination" && selectedPlace.origin
        ? "No Ember buses go there from your start."
        : "No Ember stop matches that.";
      list.appendChild(li);
    }
    items.forEach((place, i) => {
      const li = document.createElement("li");
      li.textContent = place.label;
      li.className = i === active ? "active" : "";
      // mousedown fires before the input's blur, so the click still registers
      li.addEventListener("mousedown", (e) => { e.preventDefault(); select(place); });
      list.appendChild(li);
    });
    list.hidden = false;
  }

  async function search(q) {
    if (q === lastQuery) return;
    lastQuery = q;
    let url = `${API}/api/places?q=${encodeURIComponent(q)}`;
    if (role === "destination" && selectedPlace.origin) url += `&origin=${selectedPlace.origin.id}`;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`places ${res.status}`);
      const data = await res.json();
      if (q !== lastQuery) return;              // a newer keystroke superseded this
      items = data.filter((p) => !(role === "destination" && selectedPlace.origin && p.id === selectedPlace.origin.id));
      active = -1;
      render();
    } catch (error) {
      console.error("Place search failed:", error);
    }
  }

  input.addEventListener("input", () => {
    selectedPlace[role] = null;                 // typing again means nothing is chosen yet
    input.classList.remove("invalid");
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { close(); lastQuery = ""; return; }
    timer = setTimeout(() => search(q), 180);
  });

  input.addEventListener("focus", () => {
    if (!selectedPlace[role] && input.value.trim()) { lastQuery = ""; search(input.value.trim()); }
  });

  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, items.length - 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
    else if (e.key === "Enter") { if (active >= 0) { e.preventDefault(); select(items[active]); } else if (items.length === 1) { e.preventDefault(); select(items[0]); } }
    else if (e.key === "Escape") close();
  });

  input.addEventListener("blur", () => {
    close();
    if (input.value.trim() && !selectedPlace[role]) input.classList.add("invalid");
  });
}

attachTypeahead(document.getElementById("origin"), "origin");
attachTypeahead(document.getElementById("destination"), "destination");
