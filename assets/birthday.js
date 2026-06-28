/* Sees Journal — Birthday Artist of the Day.
   Picks one artist per day, entirely client-side from the visitor's local date, so the feature
   changes every day no matter when the page was last built. If any artist's birthday (month-day)
   matches today, that artist is featured ("Born on this day"); otherwise an artist is chosen by
   day-of-year so the slot is never empty and still rotates daily.

   Images: the portrait is fetched from the artist's English Wikipedia summary and shown ONLY when
   the image is served from Wikimedia Commons (i.e. verifiably free-licensed). Non-free images and
   the copyrighted artworks themselves are never embedded — the card links out to view the work. */
(function () {
  "use strict";
  var dataEl = document.getElementById("bday-data");
  var card = document.getElementById("bday-card");
  if (!dataEl || !card) return;

  var artists;
  try {
    // the build step fills this block between HTML-comment markers; strip them before parsing
    artists = JSON.parse(dataEl.textContent.replace(/<!--[\s\S]*?-->/g, "").trim() || "[]");
  } catch (e) { return; }
  if (!Array.isArray(artists) || !artists.length) return;

  var MONTHS = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];

  var now = new Date();
  var mm = String(now.getMonth() + 1).padStart(2, "0");
  var dd = String(now.getDate()).padStart(2, "0");
  var md = mm + "-" + dd;
  var year = now.getFullYear();
  // day-of-year (1..366), used as the rotation index on non-birthday days
  var doy = Math.floor((now - new Date(year, 0, 0)) / 86400000);

  // Birthday match takes priority; ties on a shared day rotate year over year.
  var bdayMatches = artists.filter(function (a) { return (a.dob || "").slice(5) === md; });
  var artist, isBirthday;
  if (bdayMatches.length) {
    artist = bdayMatches[year % bdayMatches.length];
    isBirthday = true;
  } else {
    artist = artists[doy % artists.length];
    isBirthday = false;
  }

  function txt(id, value) { var n = document.getElementById(id); if (n) n.textContent = value || ""; }

  var p = (artist.dob || "").split("-");
  var prettyDate = (p.length === 3)
    ? MONTHS[parseInt(p[1], 10) - 1] + " " + parseInt(p[2], 10) + ", " + p[0]
    : (artist.dob || "");

  txt("bday-kicker", isBirthday ? "Born on this day" : "Artist of the day");
  txt("bday-name", artist.name);
  txt("bday-when", "Born " + prettyDate);
  txt("bday-bio", artist.bio);
  txt("bday-work", artist.work ? ("\u201C" + artist.work + "\u201D") : "");
  txt("bday-workmeta", [artist.year, artist.medium, artist.inst].filter(Boolean).join("  \u00B7  "));

  // Monogram fallback for the portrait
  var initials = (artist.name || "").split(/\s+/)
    .map(function (w) { return w.charAt(0); }).join("").slice(0, 2).toUpperCase();
  txt("bday-initials", initials);

  // Links: about the artist + a search that lands on the work (never embeds the artwork)
  var links = document.getElementById("bday-links");
  if (links) {
    var title = (artist.wiki || artist.name || "").replace(/ /g, "_");
    var aboutUrl = "https://en.wikipedia.org/wiki/" + encodeURIComponent(title);
    var workUrl = "https://en.wikipedia.org/w/index.php?search=" +
      encodeURIComponent((artist.work || "") + " " + (artist.name || ""));
    links.innerHTML = "";
    var a1 = document.createElement("a");
    a1.href = aboutUrl; a1.target = "_blank"; a1.rel = "noopener noreferrer";
    a1.className = "bday-link"; a1.textContent = "About the artist \u2197";
    var a2 = document.createElement("a");
    a2.href = workUrl; a2.target = "_blank"; a2.rel = "noopener noreferrer";
    a2.className = "bday-link bday-link--ghost"; a2.textContent = "See the work \u2197";
    links.appendChild(a1); links.appendChild(a2);
  }

  // Progressive enhancement: free-licensed portrait from Wikimedia Commons only.
  try {
    var wikiTitle = (artist.wiki || artist.name || "").replace(/ /g, "_");
    fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" + encodeURIComponent(wikiTitle))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) return;
        var src = j.thumbnail && j.thumbnail.source;
        // Commons images live under /wikipedia/commons/ and are freely licensed. Skip anything
        // served locally from en.wikipedia (/wikipedia/en/) — those may be non-free (fair use).
        if (src && src.indexOf("/commons/") !== -1) {
          var portrait = document.getElementById("bday-portrait");
          if (portrait) {
            portrait.style.backgroundImage = 'url("' + src + '")';
            portrait.classList.add("has-img");
          }
        }
      })
      .catch(function () { /* offline or blocked — monogram stays */ });
  } catch (e) { /* no fetch — monogram stays */ }
})();
