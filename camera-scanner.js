/* Self-hosted ZXing decoder; no image or video is uploaded to a third party. */
(() => {
  class AttendanceCameraScanner {
    constructor({video, onCode, onState, onError, canScan}) {
      Object.assign(this, {video, onCode, onState, onError, canScan});
      this.session = 0;
      this.active = false;
      this.stream = null;
      this.reader = null;
    }
    stop() {
      this.session += 1;
      this.active = false;
      if (this.reader) {
        this.reader.reset();
        this.reader = null;
      }
      if (this.stream) {
        this.stream.getTracks().forEach(track => track.stop());
        this.stream = null;
      }
      this.video.srcObject = null;
      this.onState('stopped');
    }
    async start() {
      if (this.active || !this.canScan()) return;
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        this.onError('Camera scanning needs HTTPS and a browser with camera access. You can still use a USB/Bluetooth scanner.');
        return;
      }
      if (!window.ZXing?.BrowserMultiFormatReader) {
        this.onError('The camera decoder could not load. Refresh the page or use a USB/Bluetooth scanner.');
        return;
      }
      this.active = true;
      const session = ++this.session;
      this.onState('requesting');
      try {
        const stream = await navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}}});
        if (session !== this.session || !this.active || !this.canScan()) {
          stream.getTracks().forEach(track => track.stop());
          if (session === this.session) this.stop();
          return;
        }
        this.stream = stream;
        stream.getVideoTracks().forEach(track => track.addEventListener('ended', () => {
          if (session === this.session && this.active) {
            this.stop();
            this.onError('Camera disconnected. Reconnect it and select Start camera, or use the handheld scanner.');
          }
        }));
        const hints = new Map([[window.ZXing.DecodeHintType.POSSIBLE_FORMATS,[window.ZXing.BarcodeFormat.CODE_128]]]);
        const reader = new window.ZXing.BrowserMultiFormatReader(hints, 300);
        reader.timeBetweenDecodingAttempts = 150;
        this.reader = reader;
        this.onState('scanning');
        await reader.decodeFromStream(stream, this.video, (result, error) => {
          if (session !== this.session || !this.active) return;
          if (result) {
            const code = result.getText().trim().toUpperCase();
            if (!/^SMP-[A-F0-9]{20}$/.test(code)) {
              this.stop();
              this.onError('This is not an SMP student tag. Use a tag printed from Student Barcodes.');
              return;
            }
            const allowed = this.canScan();
            this.stop(); // One camera session records one tag; Start camera re-arms it.
            if (allowed) this.onCode(code);
          } else if (error && ![window.ZXing.NotFoundException,window.ZXing.ChecksumException,window.ZXing.FormatException].some(type => type && error instanceof type)) {
            this.stop();
            this.onError('Could not read camera frames. Restart the camera or use the handheld scanner.');
          }
        });
        if (session !== this.session) reader.reset();
      } catch (error) {
        if (session !== this.session) return;
        this.stop();
        const messages = {
          NotAllowedError:'Camera permission was denied. Allow camera access in your browser site settings, then try again.',
          NotFoundError:'No camera was found. Connect a camera or use a USB/Bluetooth scanner.',
          NotReadableError:'The camera is busy or unavailable. Close other apps using it and try again.',
          OverconstrainedError:'This camera cannot use the requested settings. Try another camera or the handheld scanner.'
        };
        this.onError(messages[error.name] || 'Camera could not start. Check browser camera permissions or use the handheld scanner.');
      }
    }
  }
  window.AttendanceCameraScanner = AttendanceCameraScanner;
})();
