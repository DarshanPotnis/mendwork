// @ts-check
(
  /**
   * Arm or disarm the recorder around its own action, or have it commit edited fields.
   * @param {RecorderControlRequest} request
   * @returns {RecorderControlReply}
   */
  (request) => {
    const recorder = window.__mendwork?.recorder;
    if (recorder === undefined) {
      return { document: null, sequence: 0, ok: false };
    }
    switch (request.operation) {
      case "arm":
        return { document: recorder.document, sequence: 0, ok: recorder.arm(request.document, request.element) };
      case "disarm":
        recorder.disarm(request.document);
        return { document: recorder.document, sequence: 0, ok: true };
      case "flush": {
        const position = recorder.flush();
        return { document: position.document, sequence: position.sequence, ok: true };
      }
    }
  }
);
