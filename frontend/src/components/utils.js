export function format_game_time(millis) {
  var minutes = Math.floor(millis / 60000);
  var seconds = ((millis % 60000) / 1000).toFixed(0);
  if (seconds === 60) {
    minutes += 1;
    seconds = 0;
  }
  return minutes + ":" + (seconds < 10 ? '0' : '') + seconds
}

export function default_query() {
  return '?include_ladder_ids=3&include_map_ids=9&'
}
