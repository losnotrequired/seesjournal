/* ───────────────────────────────────────────────────────────────────────────
   Sees Journal — Atlas venue photos (Google Street View)

   Paste your Google Maps API key between the quotes below to turn on
   street-level storefront photos on the Atlas cards and venue pages.

   Setup (one time):
     1. Google Cloud console → enable the "Street View Static API".
     2. Create an API key, then RESTRICT it:
          • Application restriction: HTTP referrers → add your site
            (e.g.  seesjournal.com/*  and  www.seesjournal.com/* ).
          • API restriction: Street View Static API only.
        (The key is visible in page source on any static site, so the
         referrer restriction is what keeps it from being misused.)
     3. Paste it below and redeploy.

   Leave it "" and everything still works — cards fall back to the keyless
   aerial view, and venue pages simply omit the photo.
   ─────────────────────────────────────────────────────────────────────────── */
window.SEES_MAPS_KEY    = "";     /* ← your Street View Static API key */
window.SEES_ATLAS_PHOTOS = true;  /* false → Street View on venue pages only (fewer API calls) */

/* Venue detail page: drop a Street View of the address into #vpphoto.
   (The Atlas directory handles its own cards in places.html.) */
(function () {
  function streetviewUrl(lat, lng, w, h) {
    return "https://maps.googleapis.com/maps/api/streetview?size=" + w + "x" + h +
           "&location=" + lat + "," + lng +
           "&fov=80&pitch=0&source=outdoor&key=" + window.SEES_MAPS_KEY;
  }
  function init() {
    var img = document.getElementById("vpphoto");
    if (!img) return;                                  // not a venue page
    var wrap = img.closest(".vp-photo");
    var key = window.SEES_MAPS_KEY;
    var lat = img.getAttribute("data-lat");
    var lng = img.getAttribute("data-lng");
    if (!key || !lat || !lng) { if (wrap) wrap.style.display = "none"; return; }

    // Show the photo immediately (works even if the metadata call below is
    // blocked by CORS); hide the block only on a hard load failure.
    img.onerror = function () { if (wrap) wrap.style.display = "none"; };
    img.src = streetviewUrl(lat, lng, 640, 360);

    // Best-effort coverage check: if this address has no outdoor Street View,
    // hide the block instead of showing Google's grey "no imagery" tile.
    try {
      fetch("https://maps.googleapis.com/maps/api/streetview/metadata?location=" +
            lat + "," + lng + "&source=outdoor&key=" + key)
        .then(function (r) { return r.json(); })
        .then(function (d) { if (d && d.status !== "OK" && wrap) wrap.style.display = "none"; })
        .catch(function () { /* CORS/network: keep the image we already set */ });
    } catch (e) { /* no-op */ }
  }
  if (document.readyState !== "loading") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
