/** Protocol 4 playback shared by telephone and message replies. No microphone ownership. */
type AudioEvent = Record<string, any>;
interface Stream {
  response: string;
  generation: number;
  segment: number;
  rate: number;
  frame: number;
  offset: number;
}

export class StreamPlayer {
  private streams = new Map<number, Stream>();
  private sources = new Map<AudioBufferSourceNode, string>();
  private timers = new Set<number>();
  private withdrawn = new Set<string>();
  private nextTime = 0;
  private floor = 0;
  private current = "";
  private lastMeter = 0;

  constructor(
    private context: AudioContext,
    private output: AudioNode,
    private send: (data: AudioEvent) => void,
    private meter: (level: number) => void = () => {},
    private changed: (response: string, generation: number) => void = () => {},
    private error: (text: string) => void = () => {},
  ) {}

  silence(nextGeneration?: number) {
    if (nextGeneration !== undefined) this.floor = Math.max(this.floor, nextGeneration);
    for (const source of this.sources.keys()) {
      try { source.stop(); } catch { /* Already finished. */ }
    }
    this.sources.clear();
    for (const timer of this.timers) window.clearTimeout(timer);
    this.timers.clear();
    this.streams.clear();
    this.nextTime = 0;
    this.lastMeter = 0;
    this.meter(0);
  }

  event(event: AudioEvent): boolean {
    if (event.type === "audio.segment_start") {
      if (event.output_generation < this.floor || this.withdrawn.has(event.response_id)) return true;
      // Consecutive replies can arrive while the last buffered syllable is
      // still playing. Only explicit interruption/withdrawal stops that tail.
      this.current = event.response_id;
      this.streams.set(event.stream_id, {
        response: event.response_id, generation: event.output_generation,
        segment: event.segment_seq, rate: event.sample_rate, frame: 0, offset: 0,
      });
      this.changed(event.response_id, event.output_generation);
      if (this.context.state !== "running") this.error("声音未启用，点击页面后可播放；文字会继续显示。");
      return true;
    }
    if (event.type === "audio.segment_end") {
      const stream = this.streams.get(event.stream_id);
      if (!stream || stream.generation !== event.output_generation || stream.segment !== event.segment_seq
        || stream.offset !== event.final_sample_count || this.withdrawn.has(stream.response)) return true;
      if (this.context.state !== "running") return true;
      const timer = window.setTimeout(() => {
        this.timers.delete(timer);
        if (!this.withdrawn.has(stream.response) && stream.generation >= this.floor) {
          this.send({ type: "playout.ack", response_id: stream.response,
            output_generation: stream.generation, segment_seq: stream.segment,
            played_sample_offset: stream.offset, complete: true, client_audio_time: this.context.currentTime });
        }
        if (this.streams.get(event.stream_id) === stream) this.streams.delete(event.stream_id);
      }, Math.max(0, this.nextTime - this.context.currentTime) * 1000 + 35);
      this.timers.add(timer);
      return true;
    }
    if (event.type === "response.interrupted" || event.type === "response.failed") {
      if (event.response_id === this.current || [...this.sources.values()].includes(event.response_id)) this.silence();
      return true;
    }
    if (event.type === "response.withdrawn" || event.type === "retracted") {
      for (const id of event.response_ids || []) this.withdrawn.add(id);
      if (this.withdrawn.has(this.current) || [...this.sources.values()].some(id => this.withdrawn.has(id))) this.silence();
      return true;
    }
    return false;
  }

  frame(data: ArrayBuffer) {
    if (data.byteLength < 34) return;
    const view = new DataView(data);
    if (view.getUint32(0, false) !== 0x44564134) return; // DVA4
    const generation = view.getUint32(4, true);
    const stream = this.streams.get(view.getUint32(8, true));
    const seq = view.getUint32(12, true), frame = view.getUint32(16, true);
    const offset = Number(view.getBigUint64(20, true)), count = view.getUint16(28, true);
    if (!stream || generation < this.floor || generation !== stream.generation || seq !== stream.segment
      || this.withdrawn.has(stream.response)) return;
    if (frame < stream.frame) return;
    if (frame !== stream.frame || offset !== stream.offset || data.byteLength !== 32 + count * 2) {
      this.silence(); this.error("语音片段不完整，文字已保留，可稍后重播。"); return;
    }
    stream.frame++; stream.offset += count;
    if (this.context.state !== "running") return;
    const pcm = new Int16Array(data.slice(32));
    const buffer = this.context.createBuffer(1, pcm.length, stream.rate);
    const floats = buffer.getChannelData(0);
    let energy = 0;
    for (let i = 0; i < pcm.length; i++) { floats[i] = pcm[i] / 32768; energy += floats[i] ** 2; }
    const source = this.context.createBufferSource();
    source.buffer = buffer; source.connect(this.output);
    const at = this.nextTime > this.context.currentTime + .01
      ? this.nextTime : this.context.currentTime + .08;
    if (at - this.context.currentTime > .5) {
      this.silence(); this.error("声音缓冲过长，已停止积压播放。"); return;
    }
    this.nextTime = at + buffer.duration;
    if (frame === 0) {
      const timer = window.setTimeout(() => {
        this.timers.delete(timer);
        if (generation >= this.floor && !this.withdrawn.has(stream.response)) this.send({
          type: "playout.started", response_id: stream.response, output_generation: generation,
          segment_seq: stream.segment, client_audio_time: this.context.currentTime,
        });
      }, Math.max(0, at - this.context.currentTime) * 1000);
      this.timers.add(timer);
    }
    this.sources.set(source, stream.response);
    source.onended = () => { this.sources.delete(source); if (!this.sources.size) this.meter(0); };
    if (at - this.lastMeter >= .08) {
      this.lastMeter = at;
      const timer = window.setTimeout(() => {
        this.timers.delete(timer);
        if (generation >= this.floor) this.meter(Math.sqrt(energy / pcm.length));
      }, Math.max(0, at - this.context.currentTime) * 1000);
      this.timers.add(timer);
    }
    source.start(at);
  }
}
