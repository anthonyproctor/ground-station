// Ground Station tweaks for graphs1090 (added by apply-graphs1090.sh).
// Friendlier captions above each graph and a nav bar back to the station.
(function () {
  var caps = {
    'dump1090-local_trailing_rate': ['Messages per second', 'how busy the radio is'],
    'dump1090-aircraft': ['Planes seen', 'aircraft heard at the same time'],
    'dump1090-tracks': ['New flights tracked', ''],
    'dump1090-range': ['How far we can hear', 'stays empty on purpose: the decoder doesn\u2019t know where the station is (privacy). Today\u2019s max range is on the station page.'],
    'dump1090-signal': ['Signal and noise', 'stronger signal and lower noise are better'],
    'dump1090-local_rate': ['Peak rates', 'busiest moments for messages and positions'],
    'dump1090-aircraft_message_rate': ['Messages per plane', ''],
    'dump1090-cpu': ['Decoder workload', 'CPU used by readsb'],
    'dump1090-misc': ['Decoder details', ''],
    'system-cpu': ['Pi CPU', ''],
    'system-temperature': ['Pi temperature', ''],
    'system-memory': ['Pi memory', ''],
    'system-network_bandwidth': ['Network traffic', ''],
    'system-df_root': ['SD card space', ''],
    'system-disk_io_iops': ['SD card reads and writes', ''],
    'system-disk_io_octets': ['SD card throughput', '']
  };
  var panels = { 'ADS-B 1090 Graphs': 'Planes and radio', 'System Graphs': 'The Raspberry Pi' };
  function run() {
    document.title = 'Graphs · Ground Station';
    var h1 = document.querySelector('.container h1');
    if (h1) h1.textContent = 'Station Graphs';
    Object.keys(caps).forEach(function (id) {
      var img = document.getElementById(id + '-image');
      if (!img || !img.parentNode || !img.parentNode.parentNode) return;
      var c = document.createElement('div');
      c.className = 'gs-cap';
      c.textContent = caps[id][0];
      if (caps[id][1]) { var s = document.createElement('small'); s.textContent = caps[id][1]; c.appendChild(s); }
      var link = img.parentNode;
      link.parentNode.insertBefore(c, link);
      img.alt = caps[id][0];
    });
    Array.prototype.forEach.call(document.querySelectorAll('.panel-heading'), function (h) {
      var t = h.textContent.trim();
      if (panels[t]) h.textContent = panels[t];
    });
    if (!document.getElementById('gs-nav')) {
      var nav = document.createElement('nav');
      nav.id = 'gs-nav';
      nav.setAttribute('aria-label', 'Ground Station');
      nav.innerHTML = '<a href="/">← Station</a><a href="/map/">Live map</a>';
      document.body.insertBefore(nav, document.body.firstChild);
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run();
})();
