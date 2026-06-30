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
  var box = document.getElementById("vpphoto");
  if (!box) return;
  var lat = box.getAttribute("data-lat"), lng = box.getAttribute("data-lng");
  if (!lat || !lng) return;
  var loc = encodeURIComponent(lat + "," + lng);
  var name = box.getAttribute("data-name") || "this venue";
  var img = new Image();
  img.alt = "Street view of " + name;
  img.onload = function(){
    box.appendChild(img);
    var cap = document.createElement("span");
    cap.className = "vp-photo__cap";
    cap.textContent = "Street View imagery \u00a9 Google";
    box.appendChild(cap);
    box.classList.add("is-shown");
  };
  img.onerror = function(){ /* no Street View coverage here — leave hidden */ };
  img.src = "https://maps.googleapis.com/maps/api/streetview?size=640x400&location="
            + loc + "&fov=80&pitch=0&source=outdoor&return_error_code=true&key=" + STREETVIEW_KEY;
})();

(function(){
  /* Atlas directory thumbnails: lazy-load a Street View image into each venue
     card as it scrolls into view; cards with no coverage keep their monogram. */
  if (!STREETVIEW_KEY) return;
  var cards = document.querySelectorAll(".vcard[data-lat]");
  if (!cards.length) return;
  function load(card){
    var media = card.querySelector(".vcard__media");
    if (!media || media.querySelector(".vcard__photo")) return;
    var lat = card.getAttribute("data-lat"), lng = card.getAttribute("data-lng");
    if (!lat || !lng) return;
    var loc = encodeURIComponent(lat + "," + lng);
    var img = new Image();
    img.className = "vcard__photo";
    img.alt = "Street view of " + (card.getAttribute("data-name") || "this venue");
    img.onerror = function(){ /* no Street View here — monogram stays */ };
    img.onload = function(){ media.appendChild(img); };
    img.src = "https://maps.googleapis.com/maps/api/streetview?size=600x375&location="
              + loc + "&fov=80&pitch=0&source=outdoor&return_error_code=true&key=" + STREETVIEW_KEY;
  }
  if ("IntersectionObserver" in window){
    var io = new IntersectionObserver(function(entries, obs){
      entries.forEach(function(e){ if (e.isIntersecting){ load(e.target); obs.unobserve(e.target); } });
    }, {rootMargin:"200px"});
    Array.prototype.forEach.call(cards, function(c){ io.observe(c); });
  } else {
    Array.prototype.forEach.call(cards, load);
  }
})();
