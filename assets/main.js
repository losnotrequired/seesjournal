/* === Atlas venue photos — Google Street View Static =======================
   Paste your Google Maps API key below to show a front-of-building photo on
   each Atlas venue page. Leave it "" and venue pages just show the map.
   The key is visible in page source, so in the Google Cloud console restrict it
   to your domain (HTTP referrers) and enable the "Street View Static API".
   Venues with no Street View coverage stay photo-less automatically. */
var STREETVIEW_KEY = "AIzaSyBpnywBZdtHzXlCpTcruQe3Sdqie-bCDbw";

(function(){
  var menu = document.getElementById('menu');
  var hamb = document.getElementById('hamb');
  var close = document.getElementById('close');
  if (hamb && menu) hamb.addEventListener('click', function(){ menu.classList.add('open'); });
  if (close && menu) close.addEventListener('click', function(){ menu.classList.remove('open'); });
  Array.prototype.forEach.call(document.querySelectorAll('[data-menu]'), function(a){
    a.addEventListener('click', function(){ if (menu) menu.classList.remove('open'); });
  });

  var totop = document.getElementById('totop');
  if (totop){
    window.addEventListener('scroll', function(){
      if (window.scrollY > 600) totop.classList.add('show'); else totop.classList.remove('show');
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-share]'), function(a){
    a.addEventListener('click', function(e){
      e.preventDefault();
      if (navigator.share){ navigator.share({ title: document.title, url: location.href }).catch(function(){}); }
      else if (navigator.clipboard){ navigator.clipboard.writeText(location.href); a.textContent = '\u2713'; }
    });
  });

  // Placeholder guard: some venue pages serve a tiny lazy-load/spacer image that "loads"
  // successfully, so the card's inline onerror never fires and faint overlay text is left
  // sitting on a blank panel. Treat a loaded-but-tiny card photo like a failed image: drop
  // has-photo, turn the panel blue, and remove the img so the text becomes readable.
  function flagPlaceholder(img){
    var panel = img.closest('.card__panel');
    if (!panel) return;
    panel.classList.remove('has-photo');
    panel.classList.add('is-blue');
    img.remove();
  }
  function checkPhoto(img){
    if (img.naturalWidth > 0 && img.naturalWidth < 64) flagPlaceholder(img);
  }
  Array.prototype.forEach.call(document.querySelectorAll('.card__photo'), function(img){
    if (img.complete) checkPhoto(img);
    else img.addEventListener('load', function(){ checkPhoto(img); });
  });
})();

(function(){
  if (!STREETVIEW_KEY) return;

  // Compass bearing (deg) from one lat/lng to another.
  function svBearing(fLat, fLng, tLat, tLng){
    var r = Math.PI/180, p1 = fLat*r, p2 = tLat*r, dl = (tLng - fLng)*r;
    var y = Math.sin(dl)*Math.cos(p2);
    var x = Math.cos(p1)*Math.sin(p2) - Math.sin(p1)*Math.cos(p2)*Math.cos(dl);
    return (Math.atan2(y, x)*180/Math.PI + 360) % 360;
  }

  // Build a Street View image AIMED AT the building at lat/lng. First look up the
  // nearest outdoor panorama (metadata is free), then point the camera from that
  // panorama toward the building. done(img) on success; fail() when there's truly
  // no coverage. If the metadata lookup is blocked, fall back to the API's own
  // default heading (no worse than before) rather than dropping the photo.
  function svPhoto(lat, lng, size, alt, done, fail){
    var bLat = parseFloat(lat), bLng = parseFloat(lng);
    if (isNaN(bLat) || isNaN(bLng)){ if (fail) fail(); return; }
    var loc = encodeURIComponent(bLat + "," + bLng);
    function build(heading){
      var img = new Image();
      img.alt = alt;
      img.onload = function(){ done(img); };
      img.onerror = function(){ if (fail) fail(); };
      img.src = "https://maps.googleapis.com/maps/api/streetview?size=" + size +
                "&location=" + loc +
                (heading != null ? "&heading=" + heading.toFixed(1) : "") +
                "&fov=80&pitch=0&source=outdoor&return_error_code=true&key=" + STREETVIEW_KEY;
    }
    fetch("https://maps.googleapis.com/maps/api/streetview/metadata?location=" + loc +
          "&source=outdoor&key=" + STREETVIEW_KEY)
      .then(function(r){ return r.json(); })
      .then(function(m){
        if (m && m.status === "OK" && m.location){
          build(svBearing(m.location.lat, m.location.lng, bLat, bLng)); // face the building
        } else if (m && m.status === "ZERO_RESULTS"){
          if (fail) fail();                                             // genuinely no coverage
        } else {
          build(null);                                                 // unknown -> default heading
        }
      })
      .catch(function(){ build(null); });                              // blocked -> default heading
  }

  // (1) Venue detail page — storefront photo above the map.
  var box = document.getElementById("vpphoto");
  if (box && box.getAttribute("data-lat") && box.getAttribute("data-lng")){
    svPhoto(box.getAttribute("data-lat"), box.getAttribute("data-lng"), "640x400",
      "Street view of " + (box.getAttribute("data-name") || "this venue"),
      function(img){
        box.appendChild(img);
        var cap = document.createElement("span");
        cap.className = "vp-photo__cap";
        cap.textContent = "Street View imagery \u00a9 Google";
        box.appendChild(cap);
        box.classList.add("is-shown");
      });
  }

  // (2) Atlas directory — lazy-loaded thumbnail per card; monogram stays if no coverage.
  var cards = document.querySelectorAll(".vcard[data-lat]");
  if (cards.length){
    var load = function(card){
      var media = card.querySelector(".vcard__media");
      if (!media || media.querySelector(".vcard__photo")) return;
      var lat = card.getAttribute("data-lat"), lng = card.getAttribute("data-lng");
      if (!lat || !lng) return;
      svPhoto(lat, lng, "600x375",
        "Street view of " + (card.getAttribute("data-name") || "this venue"),
        function(img){ img.className = "vcard__photo"; media.appendChild(img); });
    };
    if ("IntersectionObserver" in window){
      var io = new IntersectionObserver(function(entries, obs){
        entries.forEach(function(e){ if (e.isIntersecting){ load(e.target); obs.unobserve(e.target); } });
      }, {rootMargin:"200px"});
      Array.prototype.forEach.call(cards, function(c){ io.observe(c); });
    } else {
      Array.prototype.forEach.call(cards, load);
    }
  }
})();
