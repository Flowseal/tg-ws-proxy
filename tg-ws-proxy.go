package main

/*
#cgo android LDFLAGS: -llog
#include <stdlib.h>
#include <signal.h>
#ifdef __ANDROID__
#include <android/log.h>
#endif

static void androidLogProxy(char *msg) {
#ifdef __ANDROID__
    __android_log_print(ANDROID_LOG_INFO, "TgWsProxy", "%s", msg);
#endif
}
*/
import "C"

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"io"
	"log"
	"math"
	"math/big"
	mrand "math/rand/v2"
	"net"
	"os"
	"os/signal"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"
)

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const (
	defaultPort    = 1080
	tcpNodelay     = true
	defaultRecvBuf = 256 * 1024
	defaultSendBuf = 256 * 1024
	wsPoolSize     = 4
	wsPoolMaxAge   = 90.0  // seconds — reduced from 120 to catch stale faster
	wsBridgeIdle   = 120.0 // seconds — max idle time before bridge considers WS dead

	dcFailCooldown = 30.0 // seconds
	wsFailTimeout  = 2.0  // seconds
	poolMaintainInterval = 30 // seconds — how often pool evicts stale and refills
)

var (
	recvBuf    = defaultRecvBuf
	sendBuf    = defaultSendBuf
	poolSize   = wsPoolSize
	logVerbose = false
)

// ---------------------------------------------------------------------------
// Logger
// ---------------------------------------------------------------------------

var (
	logInfo  *log.Logger
	logWarn  *log.Logger
	logError *log.Logger
	logDebug *log.Logger
)

type androidLogWriter struct{}

func (w androidLogWriter) Write(p []byte) (n int, err error) {
	os.Stderr.Write(p)
	cs := C.CString(string(p))
	C.androidLogProxy(cs)
	C.free(unsafe.Pointer(cs))
	return len(p), nil
}

func initLogging(verbose bool) {
	flags := log.Ltime
	out := androidLogWriter{}
	logInfo = log.New(out, "INFO  ", flags)
	logWarn = log.New(out, "WARN  ", flags)
	logError = log.New(out, "ERROR ", flags)
	if verbose {
		logDebug = log.New(out, "DEBUG ", flags)
	} else {
		logDebug = log.New(io.Discard, "", 0)
	}
}

// ---------------------------------------------------------------------------
// Telegram IP ranges
// ---------------------------------------------------------------------------

type ipRange struct {
	lo, hi uint32
}

var tgRanges []ipRange

func init() {
	ranges := [][2]string{
		{"185.76.151.0", "185.76.151.255"},
		{"149.154.160.0", "149.154.175.255"},
		{"91.105.192.0", "91.105.193.255"},
		{"91.108.0.0", "91.108.255.255"},
	}
	for _, r := range ranges {
		lo := ipToUint32(net.ParseIP(r[0]))
		hi := ipToUint32(net.ParseIP(r[1]))
		tgRanges = append(tgRanges, ipRange{lo, hi})
	}
}

func ipToUint32(ip net.IP) uint32 {
	ip4 := ip.To4()
	if ip4 == nil {
		return 0
	}
	return binary.BigEndian.Uint32(ip4)
}

func isTelegramIP(ipStr string) bool {
	ip := net.ParseIP(ipStr)
	if ip == nil {
		return false
	}
	n := ipToUint32(ip)
	if n == 0 {
		return false
	}
	for _, r := range tgRanges {
		if n >= r.lo && n <= r.hi {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// IP -> DC mapping
// ---------------------------------------------------------------------------

type dcInfo struct {
	dc      int
	isMedia bool
}

var ipToDC = map[string]dcInfo{
	// DC1
	"149.154.175.50": {1, false}, "149.154.175.51": {1, false},
	"149.154.175.53": {1, false}, "149.154.175.54": {1, false},
	"149.154.175.52": {1, true},
	// DC2
	"149.154.167.41":  {2, false}, "149.154.167.50": {2, false},
	"149.154.167.51":  {2, false}, "149.154.167.220": {2, false},
	"95.161.76.100":   {2, false},
	"149.154.167.151": {2, true}, "149.154.167.222": {2, true},
	"149.154.167.223": {2, true}, "149.154.162.123": {2, true},
	// DC3
	"149.154.175.100": {3, false}, "149.154.175.101": {3, false},
	"149.154.175.102": {3, true},
	// DC4
	"149.154.167.91":  {4, false}, "149.154.167.92": {4, false},
	"149.154.164.250": {4, true}, "149.154.166.120": {4, true},
	"149.154.166.121": {4, true}, "149.154.167.118": {4, true},
	"149.154.165.111": {4, true},
	// DC5
	"91.108.56.100": {5, false}, "91.108.56.101": {5, false},
	"91.108.56.116": {5, false}, "91.108.56.126": {5, false},
	"149.154.171.5": {5, false},
	"91.108.56.102": {5, true}, "91.108.56.128": {5, true},
	"91.108.56.151": {5, true},
	// DC203
	"91.105.192.100": {203, false},
}

var dcOverrides = map[int]int{
	203: 2,
}

var validProtos = map[uint32]bool{
	0xEFEFEFEF: true,
	0xEEEEEEEE: true,
	0xDDDDDDDD: true,
}

// ---------------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------------

var (
	dcOpt      map[int]string // dc -> target IP (empty string means not configured)
	dcOptMu    sync.RWMutex
	wsBlackMu  sync.RWMutex
	wsBlacklist = make(map[[2]int]bool) // [dc, isMedia(0/1)]

	dcFailMu    sync.RWMutex
	dcFailUntil = make(map[[2]int]float64) // monotonic time

	zero64 = make([]byte, 64)
)

// TLS config (skip verify, like Python version)
var tlsConfig = &tls.Config{
	InsecureSkipVerify: true,
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

type Stats struct {
	connectionsTotal       atomic.Int64
	connectionsWs          atomic.Int64
	connectionsTcpFallback atomic.Int64
	connectionsHttpReject  atomic.Int64
	connectionsPassthrough atomic.Int64
	wsErrors               atomic.Int64
	bytesUp                atomic.Int64
	bytesDown              atomic.Int64
	poolHits               atomic.Int64
	poolMisses             atomic.Int64
}

func (s *Stats) Summary() string {
	ph := s.poolHits.Load()
	pm := s.poolMisses.Load()
	return fmt.Sprintf(
		"total=%d ws=%d tcp_fb=%d http_skip=%d pass=%d err=%d pool=%d/%d up=%s down=%s",
		s.connectionsTotal.Load(),
		s.connectionsWs.Load(),
		s.connectionsTcpFallback.Load(),
		s.connectionsHttpReject.Load(),
		s.connectionsPassthrough.Load(),
		s.wsErrors.Load(),
		ph, ph+pm,
		humanBytes(s.bytesUp.Load()),
		humanBytes(s.bytesDown.Load()),
	)
}

var stats Stats

func humanBytes(n int64) string {
	abs := n
	if abs < 0 {
		abs = -abs
	}
	units := []string{"B", "KB", "MB", "GB", "TB"}
	f := float64(n)
	for i, u := range units {
		if math.Abs(f) < 1024 || i == len(units)-1 {
			return fmt.Sprintf("%.1f%s", f, u)
		}
		f /= 1024
	}
	return fmt.Sprintf("%.1f%s", f, "TB")
}

// ---------------------------------------------------------------------------
// Socket helpers
// ---------------------------------------------------------------------------

func setSockOpts(conn net.Conn) {
	tc, ok := conn.(*net.TCPConn)
	if !ok {
		return
	}
	if tcpNodelay {
		_ = tc.SetNoDelay(true)
	}
	raw, err := tc.SyscallConn()
	if err != nil {
		return
	}
	_ = raw.Control(func(fd uintptr) {
		_ = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_RCVBUF, recvBuf)
		_ = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_SNDBUF, sendBuf)
	})
}

// ---------------------------------------------------------------------------
// XOR mask (WebSocket masking)
// ---------------------------------------------------------------------------

func xorMask(data, mask []byte) []byte {
	if len(data) == 0 {
		return data
	}
	result := make([]byte, len(data))
	for i := range data {
		result[i] = data[i] ^ mask[i%4]
	}
	return result
}

// ---------------------------------------------------------------------------
// WsHandshakeError
// ---------------------------------------------------------------------------

type WsHandshakeError struct {
	StatusCode int
	StatusLine string
	Headers    map[string]string
	Location   string
}

func (e *WsHandshakeError) Error() string {
	return fmt.Sprintf("HTTP %d: %s", e.StatusCode, e.StatusLine)
}

func (e *WsHandshakeError) IsRedirect() bool {
	switch e.StatusCode {
	case 301, 302, 303, 307, 308:
		return true
	}
	return false
}

// ---------------------------------------------------------------------------
// RawWebSocket
// ---------------------------------------------------------------------------

const (
	opContinuation = 0x0
	opText         = 0x1
	opBinary       = 0x2
	opClose        = 0x8
	opPing         = 0x9
	opPong         = 0xA
)

type RawWebSocket struct {
	conn   net.Conn
	mu     sync.Mutex // write lock
	closed atomic.Bool
}

func wsConnect(ip, domain, path string, timeout float64) (*RawWebSocket, error) {
	if path == "" {
		path = "/apiws"
	}
	if timeout <= 0 {
		timeout = 10.0
	}

	dialTimeout := timeout
	if dialTimeout > 10.0 {
		dialTimeout = 10.0
	}

	dialer := &net.Dialer{
		Timeout: time.Duration(dialTimeout * float64(time.Second)),
	}

	tlsCfg := &tls.Config{
		InsecureSkipVerify: true,
		ServerName:         domain,
	}

	rawConn, err := tls.DialWithDialer(dialer, "tcp", ip+":443", tlsCfg)
	if err != nil {
		return nil, err
	}

	setSockOpts(rawConn)

	wsKeyBytes := make([]byte, 16)
	_, _ = rand.Read(wsKeyBytes)
	wsKey := base64.StdEncoding.EncodeToString(wsKeyBytes)

	req := fmt.Sprintf(
		"GET %s HTTP/1.1\r\n"+
			"Host: %s\r\n"+
			"Upgrade: websocket\r\n"+
			"Connection: Upgrade\r\n"+
			"Sec-WebSocket-Key: %s\r\n"+
			"Sec-WebSocket-Version: 13\r\n"+
			"Sec-WebSocket-Protocol: binary\r\n"+
			"Origin: https://web.telegram.org\r\n"+
			"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "+
			"AppleWebKit/537.36 (KHTML, like Gecko) "+
			"Chrome/131.0.0.0 Safari/537.36\r\n"+
			"\r\n",
		path, domain, wsKey,
	)

	_ = rawConn.SetWriteDeadline(time.Now().Add(time.Duration(timeout * float64(time.Second))))
	_, err = rawConn.Write([]byte(req))
	if err != nil {
		rawConn.Close()
		return nil, err
	}
	_ = rawConn.SetWriteDeadline(time.Time{})

	// Read HTTP response headers
	_ = rawConn.SetReadDeadline(time.Now().Add(time.Duration(timeout * float64(time.Second))))

	var responseLines []string
	buf := make([]byte, 0, 4096)
	tmp := make([]byte, 1)
	for {
		_, err := rawConn.Read(tmp)
		if err != nil {
			rawConn.Close()
			return nil, err
		}
		buf = append(buf, tmp[0])
		if len(buf) >= 2 && buf[len(buf)-2] == '\r' && buf[len(buf)-1] == '\n' {
			line := string(buf[:len(buf)-2])
			buf = buf[:0]
			if line == "" {
				break
			}
			responseLines = append(responseLines, line)
		}
		if len(buf) > 16384 {
			rawConn.Close()
			return nil, fmt.Errorf("HTTP header too large")
		}
	}
	_ = rawConn.SetReadDeadline(time.Time{})

	if len(responseLines) == 0 {
		rawConn.Close()
		return nil, &WsHandshakeError{StatusCode: 0, StatusLine: "empty response"}
	}

	firstLine := responseLines[0]
	parts := strings.SplitN(firstLine, " ", 3)
	statusCode := 0
	if len(parts) >= 2 {
		statusCode, _ = strconv.Atoi(parts[1])
	}

	if statusCode == 101 {
		ws := &RawWebSocket{conn: rawConn}
		return ws, nil
	}

	headers := make(map[string]string)
	for _, hl := range responseLines[1:] {
		idx := strings.IndexByte(hl, ':')
		if idx >= 0 {
			k := strings.TrimSpace(strings.ToLower(hl[:idx]))
			v := strings.TrimSpace(hl[idx+1:])
			headers[k] = v
		}
	}
	rawConn.Close()
	return nil, &WsHandshakeError{
		StatusCode: statusCode,
		StatusLine: firstLine,
		Headers:    headers,
		Location:   headers["location"],
	}
}

func (ws *RawWebSocket) Send(data []byte) error {
	if ws.closed.Load() {
		return fmt.Errorf("WebSocket closed")
	}
	frame := buildFrame(opBinary, data, true)
	ws.mu.Lock()
	defer ws.mu.Unlock()
	_, err := ws.conn.Write(frame)
	return err
}

func (ws *RawWebSocket) SendBatch(parts [][]byte) error {
	if ws.closed.Load() {
		return fmt.Errorf("WebSocket closed")
	}
	ws.mu.Lock()
	defer ws.mu.Unlock()
	for _, part := range parts {
		frame := buildFrame(opBinary, part, true)
		_, err := ws.conn.Write(frame)
		if err != nil {
			return err
		}
	}
	return err_nil_hack()
}

// err_nil_hack is a workaround to return nil error
func err_nil_hack() error { return nil }

func (ws *RawWebSocket) Recv() ([]byte, error) {
	for !ws.closed.Load() {
		opcode, payload, err := ws.readFrame()
		if err != nil {
			ws.closed.Store(true)
			return nil, err
		}

		switch opcode {
		case opClose:
			ws.closed.Store(true)
			closePayload := payload
			if len(closePayload) > 2 {
				closePayload = closePayload[:2]
			}
			reply := buildFrame(opClose, closePayload, true)
			ws.mu.Lock()
			_, _ = ws.conn.Write(reply)
			ws.mu.Unlock()
			return nil, io.EOF

		case opPing:
			pong := buildFrame(opPong, payload, true)
			ws.mu.Lock()
			_, _ = ws.conn.Write(pong)
			ws.mu.Unlock()
			continue

		case opPong:
			continue

		case opText, opBinary:
			return payload, nil

		default:
			continue
		}
	}
	return nil, io.EOF
}

func (ws *RawWebSocket) Close() {
	if ws.closed.Swap(true) {
		return
	}
	frame := buildFrame(opClose, nil, true)
	ws.mu.Lock()
	_, _ = ws.conn.Write(frame)
	ws.mu.Unlock()
	_ = ws.conn.Close()
}

func buildFrame(opcode int, data []byte, mask bool) []byte {
	length := len(data)
	fb := byte(0x80 | opcode)

	var header []byte

	if !mask {
		if length < 126 {
			header = []byte{fb, byte(length)}
		} else if length < 65536 {
			header = make([]byte, 4)
			header[0] = fb
			header[1] = 126
			binary.BigEndian.PutUint16(header[2:], uint16(length))
		} else {
			header = make([]byte, 10)
			header[0] = fb
			header[1] = 127
			binary.BigEndian.PutUint64(header[2:], uint64(length))
		}
		result := make([]byte, len(header)+length)
		copy(result, header)
		copy(result[len(header):], data)
		return result
	}

	maskKey := make([]byte, 4)
	_, _ = rand.Read(maskKey)
	masked := xorMask(data, maskKey)

	if length < 126 {
		header = make([]byte, 6)
		header[0] = fb
		header[1] = byte(0x80 | length)
		copy(header[2:6], maskKey)
	} else if length < 65536 {
		header = make([]byte, 8)
		header[0] = fb
		header[1] = byte(0x80 | 126)
		binary.BigEndian.PutUint16(header[2:4], uint16(length))
		copy(header[4:8], maskKey)
	} else {
		header = make([]byte, 14)
		header[0] = fb
		header[1] = byte(0x80 | 127)
		binary.BigEndian.PutUint64(header[2:10], uint64(length))
		copy(header[10:14], maskKey)
	}

	result := make([]byte, len(header)+len(masked))
	copy(result, header)
	copy(result[len(header):], masked)
	return result
}

func (ws *RawWebSocket) readFrame() (int, []byte, error) {
	hdr := make([]byte, 2)
	if _, err := io.ReadFull(ws.conn, hdr); err != nil {
		return 0, nil, err
	}

	opcode := int(hdr[0] & 0x0F)
	length := uint64(hdr[1] & 0x7F)

	if length == 126 {
		buf := make([]byte, 2)
		if _, err := io.ReadFull(ws.conn, buf); err != nil {
			return 0, nil, err
		}
		length = uint64(binary.BigEndian.Uint16(buf))
	} else if length == 127 {
		buf := make([]byte, 8)
		if _, err := io.ReadFull(ws.conn, buf); err != nil {
			return 0, nil, err
		}
		length = binary.BigEndian.Uint64(buf)
	}

	hasMask := (hdr[1] & 0x80) != 0
	var maskKey []byte
	if hasMask {
		maskKey = make([]byte, 4)
		if _, err := io.ReadFull(ws.conn, maskKey); err != nil {
			return 0, nil, err
		}
	}

	payload := make([]byte, length)
	if length > 0 {
		if _, err := io.ReadFull(ws.conn, payload); err != nil {
			return 0, nil, err
		}
	}

	if hasMask {
		payload = xorMask(payload, maskKey)
	}

	return opcode, payload, nil
}

// ---------------------------------------------------------------------------
// Crypto helpers: DC extraction & patching
// ---------------------------------------------------------------------------

func newAESCTR(key, iv []byte) (cipher.Stream, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewCTR(block, iv), nil
}

func dcFromInit(data []byte) (dc int, isMedia bool, ok bool) {
	if len(data) < 64 {
		return 0, false, false
	}

	stream, err := newAESCTR(data[8:40], data[40:56])
	if err != nil {
		logDebug.Printf("DC extraction failed: %v", err)
		return 0, false, false
	}

	keystream := make([]byte, 64)
	stream.XORKeyStream(keystream, zero64)

	// XOR bytes 56..64 of data with keystream
	plain := make([]byte, 8)
	for i := 0; i < 8; i++ {
		plain[i] = data[56+i] ^ keystream[56+i]
	}

	proto := binary.LittleEndian.Uint32(plain[0:4])
	dcRaw := int16(binary.LittleEndian.Uint16(plain[4:6]))

	logDebug.Printf("dc_from_init: proto=0x%08X dc_raw=%d plain=%x", proto, dcRaw, plain)

	if !validProtos[proto] {
		return 0, false, false
	}

	dcAbs := int(dcRaw)
	if dcAbs < 0 {
		dcAbs = -dcAbs
	}
	media := dcRaw < 0

	if (dcAbs >= 1 && dcAbs <= 5) || dcAbs == 203 {
		return dcAbs, media, true
	}

	return 0, false, false
}

func patchInitDC(data []byte, dc int) []byte {
	if len(data) < 64 {
		return data
	}

	newDC := make([]byte, 2)
	binary.LittleEndian.PutUint16(newDC, uint16(int16(dc)))

	stream, err := newAESCTR(data[8:40], data[40:56])
	if err != nil {
		return data
	}

	ks := make([]byte, 64)
	stream.XORKeyStream(ks, zero64)

	patched := make([]byte, len(data))
	copy(patched, data)
	patched[60] = ks[60] ^ newDC[0]
	patched[61] = ks[61] ^ newDC[1]

	logDebug.Printf("init patched: dc_id -> %d", dc)
	return patched
}

// ---------------------------------------------------------------------------
// MsgSplitter
// ---------------------------------------------------------------------------

type MsgSplitter struct {
	stream cipher.Stream
}

func newMsgSplitter(initData []byte) (*MsgSplitter, error) {
	if len(initData) < 56 {
		return nil, fmt.Errorf("init data too short")
	}
	stream, err := newAESCTR(initData[8:40], initData[40:56])
	if err != nil {
		return nil, err
	}
	// skip init packet (64 bytes of keystream)
	skip := make([]byte, 64)
	stream.XORKeyStream(skip, zero64)

	return &MsgSplitter{stream: stream}, nil
}

func (s *MsgSplitter) Split(chunk []byte) [][]byte {
	plain := make([]byte, len(chunk))
	s.stream.XORKeyStream(plain, chunk)

	var boundaries []int
	pos := 0
	plainLen := len(plain)

	for pos < plainLen {
		first := plain[pos]
		var msgLen int
		if first == 0x7f {
			if pos+4 > plainLen {
				break
			}
			// 3-byte little-endian length
			msgLen = int(uint32(plain[pos+1]) | uint32(plain[pos+2])<<8 | uint32(plain[pos+3])<<16)
			msgLen *= 4
			pos += 4
		} else {
			msgLen = int(first) * 4
			pos += 1
		}
		if msgLen == 0 || pos+msgLen > plainLen {
			break
		}
		pos += msgLen
		boundaries = append(boundaries, pos)
	}

	if len(boundaries) <= 1 {
		return [][]byte{chunk}
	}

	parts := make([][]byte, 0, len(boundaries)+1)
	prev := 0
	for _, b := range boundaries {
		parts = append(parts, chunk[prev:b])
		prev = b
	}
	if prev < len(chunk) {
		parts = append(parts, chunk[prev:])
	}
	return parts
}

// ---------------------------------------------------------------------------
// WS domains
// ---------------------------------------------------------------------------

func wsDomains(dc int, isMedia *bool) []string {
	effectiveDC := dc
	if override, ok := dcOverrides[dc]; ok {
		effectiveDC = override
	}

	if isMedia == nil || *isMedia {
		return []string{
			fmt.Sprintf("kws%d-1.web.telegram.org", effectiveDC),
			fmt.Sprintf("kws%d.web.telegram.org", effectiveDC),
		}
	}
	return []string{
		fmt.Sprintf("kws%d.web.telegram.org", effectiveDC),
		fmt.Sprintf("kws%d-1.web.telegram.org", effectiveDC),
	}
}

// ---------------------------------------------------------------------------
// WsPool
// ---------------------------------------------------------------------------

type poolEntry struct {
	ws      *RawWebSocket
	created float64 // monotonic seconds
}

type WsPool struct {
	mu        sync.Mutex
	idle      map[[2]int][]poolEntry // [dc, isMedia01]
	refilling map[[2]int]bool
}

func newWsPool() *WsPool {
	return &WsPool{
		idle:      make(map[[2]int][]poolEntry),
		refilling: make(map[[2]int]bool),
	}
}

func isMediaInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

func monoNow() float64 {
	return float64(time.Now().UnixNano()) / 1e9
}

func (p *WsPool) Get(dc int, isMedia bool, targetIP string, domains []string) *RawWebSocket {
	key := [2]int{dc, isMediaInt(isMedia)}
	now := monoNow()

	p.mu.Lock()
	bucket := p.idle[key]
	for len(bucket) > 0 {
		entry := bucket[0]
		bucket = bucket[1:]
		p.idle[key] = bucket

		age := now - entry.created
		if age > wsPoolMaxAge || entry.ws.closed.Load() {
			go entry.ws.Close()
			continue
		}

		stats.poolHits.Add(1)
		logDebug.Printf("WS pool hit for DC%d%s (age=%.1fs, left=%d)",
			dc, mediaTag(isMedia), age, len(bucket))
		p.scheduleRefill(key, targetIP, domains)
		p.mu.Unlock()
		return entry.ws
	}
	p.mu.Unlock()

	stats.poolMisses.Add(1)
	p.scheduleRefill(key, targetIP, domains)
	return nil
}

func (p *WsPool) scheduleRefill(key [2]int, targetIP string, domains []string) {
	// Must be called with p.mu held or be safe
	if p.refilling[key] {
		return
	}
	p.refilling[key] = true
	go p.refill(key, targetIP, domains)
}

func (p *WsPool) refill(key [2]int, targetIP string, domains []string) {
	dc := key[0]
	isMedia := key[1] == 1

	defer func() {
		p.mu.Lock()
		delete(p.refilling, key)
		p.mu.Unlock()
	}()

	p.mu.Lock()
	bucket := p.idle[key]
	needed := poolSize - len(bucket)
	p.mu.Unlock()

	if needed <= 0 {
		return
	}

	type result struct {
		ws *RawWebSocket
	}

	ch := make(chan result, needed)
	for i := 0; i < needed; i++ {
		go func() {
			ws := connectOneWS(targetIP, domains)
			ch <- result{ws}
		}()
	}

	for i := 0; i < needed; i++ {
		r := <-ch
		if r.ws != nil {
			p.mu.Lock()
			p.idle[key] = append(p.idle[key], poolEntry{r.ws, monoNow()})
			p.mu.Unlock()
		}
	}

	p.mu.Lock()
	logDebug.Printf("WS pool refilled DC%d%s: %d ready",
		dc, mediaTag(isMedia), len(p.idle[key]))
	p.mu.Unlock()
}

func connectOneWS(targetIP string, domains []string) *RawWebSocket {
	for _, domain := range domains {
		ws, err := wsConnect(targetIP, domain, "/apiws", 8)
		if err != nil {
			if wsErr, ok := err.(*WsHandshakeError); ok && wsErr.IsRedirect() {
				continue
			}
			return nil
		}
		return ws
	}
	return nil
}

func (p *WsPool) Warmup(dcOptMap map[int]string) {
	for dc, targetIP := range dcOptMap {
		if targetIP == "" {
			continue
		}
		for _, isMedia := range []bool{false, true} {
			domains := wsDomains(dc, &isMedia)
			key := [2]int{dc, isMediaInt(isMedia)}
			p.mu.Lock()
			p.scheduleRefill(key, targetIP, domains)
			p.mu.Unlock()
		}
	}
	logInfo.Printf("WS pool warmup started for %d DC(s)", len(dcOptMap))
}

// Maintain periodically evicts stale WS connections and refills pools
func (p *WsPool) Maintain(ctx context.Context, dcOptMap map[int]string) {
	ticker := time.NewTicker(poolMaintainInterval * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			now := monoNow()
			p.mu.Lock()
			for key, bucket := range p.idle {
				var fresh []poolEntry
				for _, e := range bucket {
					age := now - e.created
					if age > wsPoolMaxAge || e.ws.closed.Load() {
						go e.ws.Close()
					} else {
						fresh = append(fresh, e)
					}
				}
				p.idle[key] = fresh
			}
			p.mu.Unlock()

			// Refill all known DCs
			for dc, targetIP := range dcOptMap {
				if targetIP == "" {
					continue
				}
				for _, isMedia := range []bool{false, true} {
					domains := wsDomains(dc, &isMedia)
					key := [2]int{dc, isMediaInt(isMedia)}
					p.mu.Lock()
					p.scheduleRefill(key, targetIP, domains)
					p.mu.Unlock()
				}
			}
		}
	}
}

var wsPool = newWsPool()

// ---------------------------------------------------------------------------
// Helper tags
// ---------------------------------------------------------------------------

func mediaTag(isMedia bool) string {
	if isMedia {
		return "m"
	}
	return ""
}

func boolPtr(b bool) *bool {
	return &b
}

// ---------------------------------------------------------------------------
// HTTP detection
// ---------------------------------------------------------------------------

func isHTTPTransport(data []byte) bool {
	return bytes.HasPrefix(data, []byte("POST ")) ||
		bytes.HasPrefix(data, []byte("GET ")) ||
		bytes.HasPrefix(data, []byte("HEAD ")) ||
		bytes.HasPrefix(data, []byte("OPTIONS "))
}

// ---------------------------------------------------------------------------
// SOCKS5 reply
// ---------------------------------------------------------------------------

func socks5Reply(status byte) []byte {
	return []byte{0x05, status, 0x00, 0x01, 0, 0, 0, 0, 0, 0}
}

// ---------------------------------------------------------------------------
// Bridging: TCP <-> WebSocket
// ---------------------------------------------------------------------------

func bridgeWS(ctx context.Context, conn net.Conn, ws *RawWebSocket,
	label string, dc int, dst string, port int, isMedia bool,
	splitter *MsgSplitter) {

	dcTag := fmt.Sprintf("DC%d%s", dc, mediaTag(isMedia))
	dstTag := fmt.Sprintf("%s:%d", dst, port)

	var upBytes, downBytes, upPkts, downPkts int64
	startTime := time.Now()

	ctx2, cancel := context.WithCancel(ctx)
	defer cancel()

	var wg sync.WaitGroup
	wg.Add(2)

	// tcp -> ws
	go func() {
		defer wg.Done()
		defer cancel()
		buf := make([]byte, 65536)
		for {
			select {
			case <-ctx2.Done():
				return
			default:
			}
			_ = conn.SetReadDeadline(time.Now().Add(time.Duration(wsBridgeIdle * float64(time.Second))))
			n, err := conn.Read(buf)
			if n > 0 {
				chunk := buf[:n]
				stats.bytesUp.Add(int64(n))
				upBytes += int64(n)
				upPkts++

				if splitter != nil {
					parts := splitter.Split(chunk)
					if len(parts) > 1 {
						if err2 := ws.SendBatch(parts); err2 != nil {
							return
						}
					} else {
						if err2 := ws.Send(parts[0]); err2 != nil {
							return
						}
					}
				} else {
					if err2 := ws.Send(chunk); err2 != nil {
						return
					}
				}
			}
			if err != nil {
				return
			}
		}
	}()

	// ws -> tcp
	go func() {
		defer wg.Done()
		defer cancel()
		for {
			select {
			case <-ctx2.Done():
				return
			default:
			}
			_ = ws.conn.SetReadDeadline(time.Now().Add(time.Duration(wsBridgeIdle * float64(time.Second))))
			data, err := ws.Recv()
			if err != nil || data == nil {
				return
			}
			n := len(data)
			stats.bytesDown.Add(int64(n))
			downBytes += int64(n)
			downPkts++
			if _, err := conn.Write(data); err != nil {
				return
			}
		}
	}()

	wg.Wait()

	elapsed := time.Since(startTime).Seconds()
	logInfo.Printf("[%s] %s (%s) WS session closed: ^%s (%d pkts) v%s (%d pkts) in %.1fs",
		label, dcTag, dstTag,
		humanBytes(upBytes), upPkts,
		humanBytes(downBytes), downPkts,
		elapsed)

	ws.Close()
	conn.Close()
}

// ---------------------------------------------------------------------------
// Bridging: TCP <-> TCP (fallback)
// ---------------------------------------------------------------------------

func bridgeTCP(ctx context.Context, client, remote net.Conn,
	label string, dc int, dst string, port int, isMedia bool) {

	ctx2, cancel := context.WithCancel(ctx)
	defer cancel()

	var wg sync.WaitGroup
	wg.Add(2)

	forward := func(src, dstW net.Conn, isUp bool) {
		defer wg.Done()
		defer cancel()
		buf := make([]byte, 65536)
		for {
			select {
			case <-ctx2.Done():
				return
			default:
			}
			n, err := src.Read(buf)
			if n > 0 {
				if isUp {
					stats.bytesUp.Add(int64(n))
				} else {
					stats.bytesDown.Add(int64(n))
				}
				if _, werr := dstW.Write(buf[:n]); werr != nil {
					return
				}
			}
			if err != nil {
				return
			}
		}
	}

	go forward(client, remote, true)
	go forward(remote, client, false)

	wg.Wait()
	client.Close()
	remote.Close()
}

// ---------------------------------------------------------------------------
// TCP fallback
// ---------------------------------------------------------------------------

func tcpFallback(ctx context.Context, client net.Conn, dst string, port int,
	init []byte, label string, dc int, isMedia bool) bool {

	dialer := &net.Dialer{Timeout: 10 * time.Second}
	remote, err := dialer.DialContext(ctx, "tcp", fmt.Sprintf("%s:%d", dst, port))
	if err != nil {
		logWarn.Printf("[%s] TCP fallback connect to %s:%d failed: %v",
			label, dst, port, err)
		return false
	}

	stats.connectionsTcpFallback.Add(1)
	_, _ = remote.Write(init)
	bridgeTCP(ctx, client, remote, label, dc, dst, port, isMedia)
	return true
}

// ---------------------------------------------------------------------------
// Pipe (non-Telegram passthrough)
// ---------------------------------------------------------------------------

func pipe(ctx context.Context, src, dst net.Conn) {
	buf := make([]byte, 65536)
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		n, err := src.Read(buf)
		if n > 0 {
			if _, werr := dst.Write(buf[:n]); werr != nil {
				return
			}
		}
		if err != nil {
			return
		}
	}
}

// ---------------------------------------------------------------------------
// SOCKS5 client handler
// ---------------------------------------------------------------------------

func readExactly(conn net.Conn, n int, timeout time.Duration) ([]byte, error) {
	if timeout > 0 {
		_ = conn.SetReadDeadline(time.Now().Add(timeout))
		defer conn.SetReadDeadline(time.Time{})
	}
	buf := make([]byte, n)
	_, err := io.ReadFull(conn, buf)
	return buf, err
}

func handleClient(ctx context.Context, conn net.Conn) {
	stats.connectionsTotal.Add(1)
	peer := conn.RemoteAddr().String()
	label := peer

	setSockOpts(conn)

	defer func() {
		conn.Close()
	}()

	// -- SOCKS5 greeting --
	hdr, err := readExactly(conn, 2, 10*time.Second)
	if err != nil {
		logDebug.Printf("[%s] read greeting failed: %v", label, err)
		return
	}
	if hdr[0] != 5 {
		logDebug.Printf("[%s] not SOCKS5 (ver=%d)", label, hdr[0])
		return
	}
	nmethods := int(hdr[1])
	if _, err := readExactly(conn, nmethods, 10*time.Second); err != nil {
		return
	}
	if _, err := conn.Write([]byte{0x05, 0x00}); err != nil {
		return
	}

	// -- SOCKS5 CONNECT request --
	req, err := readExactly(conn, 4, 10*time.Second)
	if err != nil {
		return
	}
	cmd := req[1]
	atyp := req[3]

	if cmd != 1 {
		conn.Write(socks5Reply(0x07))
		return
	}

	var dst string
	switch atyp {
	case 1: // IPv4
		raw, err := readExactly(conn, 4, 10*time.Second)
		if err != nil {
			return
		}
		dst = net.IP(raw).String()
	case 3: // domain
		dlenBuf, err := readExactly(conn, 1, 10*time.Second)
		if err != nil {
			return
		}
		domBytes, err := readExactly(conn, int(dlenBuf[0]), 10*time.Second)
		if err != nil {
			return
		}
		dst = string(domBytes)
	case 4: // IPv6
		raw, err := readExactly(conn, 16, 10*time.Second)
		if err != nil {
			return
		}
		dst = net.IP(raw).String()
	default:
		conn.Write(socks5Reply(0x08))
		return
	}

	portBuf, err := readExactly(conn, 2, 10*time.Second)
	if err != nil {
		return
	}
	port := int(binary.BigEndian.Uint16(portBuf))

	if strings.Contains(dst, ":") {
		logError.Printf("[%s] IPv6 address detected: %s:%d — "+
			"IPv6 addresses are not supported; "+
			"disable IPv6 to continue using the proxy.",
			label, dst, port)
		conn.Write(socks5Reply(0x05))
		return
	}

	// -- Non-Telegram IP -> direct passthrough --
	if !isTelegramIP(dst) {
		stats.connectionsPassthrough.Add(1)
		logDebug.Printf("[%s] passthrough -> %s:%d", label, dst, port)

		dialer := &net.Dialer{Timeout: 10 * time.Second}
		remote, err := dialer.DialContext(ctx, "tcp", fmt.Sprintf("%s:%d", dst, port))
		if err != nil {
			logWarn.Printf("[%s] passthrough failed to %s: %T: %v", label, dst, err, err)
			conn.Write(socks5Reply(0x05))
			return
		}

		conn.Write(socks5Reply(0x00))

		ctx2, cancel := context.WithCancel(ctx)
		defer cancel()

		var wg sync.WaitGroup
		wg.Add(2)
		go func() { defer wg.Done(); pipe(ctx2, conn, remote); cancel() }()
		go func() { defer wg.Done(); pipe(ctx2, remote, conn); cancel() }()
		wg.Wait()
		remote.Close()
		return
	}

	// -- Telegram DC: accept SOCKS, read init --
	conn.Write(socks5Reply(0x00))

	init, err := readExactly(conn, 64, 15*time.Second)
	if err != nil {
		logDebug.Printf("[%s] client disconnected before init: %v", label, err)
		return
	}

	// HTTP transport -> reject
	if isHTTPTransport(init) {
		stats.connectionsHttpReject.Add(1)
		logDebug.Printf("[%s] HTTP transport to %s:%d (rejected)", label, dst, port)
		return
	}

	// -- Extract DC ID --
	dc, isMedia, dcOk := dcFromInit(init)
	initPatched := false
	var isMediaPtr *bool
	if dcOk {
		isMediaPtr = &isMedia
	}

	// Android with useSecret=0 has random dc_id bytes — patch it
	if !dcOk {
		if info, found := ipToDC[dst]; found {
			dc = info.dc
			isMedia = info.isMedia
			isMediaPtr = &isMedia
			dcOk = true

			dcOptMu.RLock()
			_, hasDC := dcOpt[dc]
			dcOptMu.RUnlock()

			if hasDC {
				signedDC := dc
				if isMedia {
					signedDC = dc
				} else {
					signedDC = -dc
				}
				init = patchInitDC(init, signedDC)
				initPatched = true
			}
		}
	}

	dcOptMu.RLock()
	_, dcConfigured := dcOpt[dc]
	dcOptMu.RUnlock()

	if !dcOk || !dcConfigured {
		logWarn.Printf("[%s] unknown DC%d for %s:%d -> TCP passthrough", label, dc, dst, port)
		tcpFallback(ctx, conn, dst, port, init, label, dc, isMedia)
		return
	}

	dcKey := [2]int{dc, isMediaInt(isMedia)}
	now := monoNow()

	mTag := ""
	if isMediaPtr == nil {
		mTag = " media?"
	} else if *isMediaPtr {
		mTag = " media"
	}

	// -- WS blacklist check --
	wsBlackMu.RLock()
	blacklisted := wsBlacklist[dcKey]
	wsBlackMu.RUnlock()

	if blacklisted {
		logDebug.Printf("[%s] DC%d%s WS blacklisted -> TCP %s:%d",
			label, dc, mTag, dst, port)
		ok := tcpFallback(ctx, conn, dst, port, init, label, dc, isMedia)
		if ok {
			logInfo.Printf("[%s] DC%d%s TCP fallback closed", label, dc, mTag)
		}
		return
	}

	// -- Try WebSocket --
	dcFailMu.RLock()
	failUntil := dcFailUntil[dcKey]
	dcFailMu.RUnlock()

	wsTimeout := 10.0
	if now < failUntil {
		wsTimeout = wsFailTimeout
	}

	isMediaForDomains := isMedia
	domains := wsDomains(dc, &isMediaForDomains)

	dcOptMu.RLock()
	target := dcOpt[dc]
	dcOptMu.RUnlock()

	var ws *RawWebSocket
	wsFailedRedirect := false
	allRedirects := true

	ws = wsPool.Get(dc, isMedia, target, domains)
	if ws != nil {
		logInfo.Printf("[%s] DC%d%s (%s:%d) -> pool hit via %s",
			label, dc, mTag, dst, port, target)
	} else {
		for _, domain := range domains {
			url := fmt.Sprintf("wss://%s/apiws", domain)
			logInfo.Printf("[%s] DC%d%s (%s:%d) -> %s via %s",
				label, dc, mTag, dst, port, url, target)

			var connErr error
			ws, connErr = wsConnect(target, domain, "/apiws", wsTimeout)
			if connErr == nil {
				allRedirects = false
				break
			}

			stats.wsErrors.Add(1)

			if wsErr, ok := connErr.(*WsHandshakeError); ok {
				if wsErr.IsRedirect() {
					wsFailedRedirect = true
					logWarn.Printf("[%s] DC%d%s got %d from %s -> %s",
						label, dc, mTag, wsErr.StatusCode, domain,
						wsErr.Location)
					continue
				}
				allRedirects = false
				logWarn.Printf("[%s] DC%d%s WS handshake: %s",
					label, dc, mTag, wsErr.StatusLine)
			} else {
				allRedirects = false
				errStr := connErr.Error()
				if strings.Contains(errStr, "certificate") ||
					strings.Contains(errStr, "hostname") {
					logWarn.Printf("[%s] DC%d%s SSL error: %v",
						label, dc, mTag, connErr)
				} else {
					logWarn.Printf("[%s] DC%d%s WS connect failed: %v",
						label, dc, mTag, connErr)
				}
			}
		}
	}

	// -- WS failed -> fallback --
	if ws == nil {
		if wsFailedRedirect && allRedirects {
			wsBlackMu.Lock()
			wsBlacklist[dcKey] = true
			wsBlackMu.Unlock()
			logWarn.Printf("[%s] DC%d%s blacklisted for WS (all 302)",
				label, dc, mTag)
		} else if wsFailedRedirect {
			dcFailMu.Lock()
			dcFailUntil[dcKey] = now + dcFailCooldown
			dcFailMu.Unlock()
		} else {
			dcFailMu.Lock()
			dcFailUntil[dcKey] = now + dcFailCooldown
			dcFailMu.Unlock()
			logInfo.Printf("[%s] DC%d%s WS cooldown for %ds",
				label, dc, mTag, int(dcFailCooldown))
		}

		logInfo.Printf("[%s] DC%d%s -> TCP fallback to %s:%d",
			label, dc, mTag, dst, port)
		ok := tcpFallback(ctx, conn, dst, port, init, label, dc, isMedia)
		if ok {
			logInfo.Printf("[%s] DC%d%s TCP fallback closed", label, dc, mTag)
		}
		return
	}

	// -- WS success --
	dcFailMu.Lock()
	delete(dcFailUntil, dcKey)
	dcFailMu.Unlock()

	stats.connectionsWs.Add(1)

	var splitter *MsgSplitter
	if initPatched {
		splitter, _ = newMsgSplitter(init)
	}

	// Send init packet
	if err := ws.Send(init); err != nil {
		logWarn.Printf("[%s] failed to send init via WS: %v", label, err)
		ws.Close()
		tcpFallback(ctx, conn, dst, port, init, label, dc, isMedia)
		return
	}

	// Bidirectional bridge
	bridgeWS(ctx, conn, ws, label, dc, dst, port, isMedia, splitter)
}

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

type ProxyServer struct {
	listener net.Listener
	ctx      context.Context
	cancel   context.CancelFunc
	wg       sync.WaitGroup
}

func runProxy(ctx context.Context, host string, port int, dcOptMap map[int]string) error {
	dcOptMu.Lock()
	dcOpt = dcOptMap
	dcOptMu.Unlock()

	addr := fmt.Sprintf("%s:%d", host, port)
	lc := net.ListenConfig{}

	listener, err := lc.Listen(ctx, "tcp", addr)
	if err != nil {
		return fmt.Errorf("listen on %s: %w", addr, err)
	}

	// Set TCP_NODELAY on listening socket if possible
	if tcpL, ok := listener.(*net.TCPListener); ok {
		raw, err := tcpL.SyscallConn()
		if err == nil {
			_ = raw.Control(func(fd uintptr) {
				_ = syscall.SetsockoptInt(int(fd), syscall.IPPROTO_TCP, syscall.TCP_NODELAY, 1)
			})
		}
	}

	srvCtx, srvCancel := context.WithCancel(ctx)
	srv := &ProxyServer{
		listener: listener,
		ctx:      srvCtx,
		cancel:   srvCancel,
	}

	logInfo.Println(strings.Repeat("=", 60))
	logInfo.Println("  Telegram WS Bridge Proxy")
	logInfo.Printf("  Listening on   %s:%d", host, port)
	logInfo.Println("  Target DC IPs:")
	for dc, ip := range dcOptMap {
		logInfo.Printf("    DC%d: %s", dc, ip)
	}
	logInfo.Println(strings.Repeat("=", 60))
	logInfo.Printf("  Configure Telegram Desktop:")
	logInfo.Printf("    SOCKS5 proxy -> %s:%d  (no user/pass)", host, port)
	logInfo.Println(strings.Repeat("=", 60))

	// Stats logger
	go func() {
		ticker := time.NewTicker(60 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-srvCtx.Done():
				return
			case <-ticker.C:
				wsBlackMu.RLock()
				var blParts []string
				for k := range wsBlacklist {
					m := ""
					if k[1] == 1 {
						m = "m"
					}
					blParts = append(blParts, fmt.Sprintf("DC%d%s", k[0], m))
				}
				wsBlackMu.RUnlock()
				bl := "none"
				if len(blParts) > 0 {
					bl = strings.Join(blParts, ", ")
				}
				logInfo.Printf("stats: %s | ws_bl: %s", stats.Summary(), bl)
			}
		}
	}()

	// Warmup WS pool
	wsPool.Warmup(dcOptMap)

	// Periodic pool maintenance
	go wsPool.Maintain(srvCtx, dcOptMap)

	// Accept loop
	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				select {
				case <-srvCtx.Done():
					return
				default:
					if ne, ok := err.(net.Error); ok && ne.Timeout() {
						continue
					}
					logError.Printf("accept error: %v", err)
					return
				}
			}
			srv.wg.Add(1)
			go func() {
				defer srv.wg.Done()
				handleClient(srvCtx, conn)
			}()
		}
	}()

	// Wait for context cancellation (graceful shutdown)
	<-srvCtx.Done()
	logInfo.Println("Shutting down proxy server...")
	listener.Close()

	// Wait for all active connections with a timeout
	done := make(chan struct{})
	go func() {
		srv.wg.Wait()
		close(done)
	}()

	select {
	case <-done:
		logInfo.Println("All connections closed gracefully")
	case <-time.After(30 * time.Second):
		logWarn.Println("Graceful shutdown timed out after 30s, forcing exit")
	}

	logInfo.Printf("Final stats: %s", stats.Summary())
	return nil
}

// ---------------------------------------------------------------------------
// Parse DC:IP list
// ---------------------------------------------------------------------------

func randomIPFromCIDR(cidr string) (string, error) {
	ip, ipnet, err := net.ParseCIDR(cidr)
	if err != nil {
		return "", err
	}
	ip = ip.To4()
	if ip == nil {
		return "", fmt.Errorf("not ipv4")
	}
	
	start := binary.BigEndian.Uint32(ip)
	mask := binary.BigEndian.Uint32(ipnet.Mask)
	
	wildcard := ^mask
	offset := uint32(1)
	if wildcard > 1 {
		offset = 1 + mrand.Uint32N(wildcard-1) 
	}
	
	randIP := start + offset
	res := make(net.IP, 4)
	binary.BigEndian.PutUint32(res, randIP)
	return res.String(), nil
}

func parseCIDRPool(cidrsStr string) (map[int]string, error) {
	result := make(map[int]string)
	ranges := strings.Split(cidrsStr, ",")
	var validCIDRs []string
	for _, r := range ranges {
		r = strings.TrimSpace(r)
		if r != "" {
			validCIDRs = append(validCIDRs, r)
		}
	}
	if len(validCIDRs) == 0 {
		validCIDRs = []string{"149.154.167.220/32"} // Fallback
	}

	dcs := []int{1, 2, 3, 4, 5, 203}
	for _, dc := range dcs {
		cidr := validCIDRs[mrand.IntN(len(validCIDRs))]
		ipStr, err := randomIPFromCIDR(cidr)
		if err == nil {
			result[dc] = ipStr
		} else {
             if net.ParseIP(cidr) != nil {
                 result[dc] = cidr
             } else {
                 result[dc] = "149.154.167.220"
             }
        }
	}
	return result, nil
}

// ---------------------------------------------------------------------------
// CGO exports for Android .so
// ---------------------------------------------------------------------------

var (
	globalCtx    context.Context
	globalCancel context.CancelFunc
	globalMu     sync.Mutex
)

//export StartProxy
func StartProxy(cHost *C.char, port C.int, cDcIps *C.char, verbose C.int) C.int {
	globalMu.Lock()
	defer globalMu.Unlock()

	if globalCancel != nil {
		// Already running
		return -1
	}

	host := C.GoString(cHost)
	goPort := int(port)
	dcIpsStr := C.GoString(cDcIps)
	isVerbose := int(verbose) != 0

	initLogging(isVerbose)

	// Passed string is a comma-separated list of CIDRs
	dcOptMap, err := parseCIDRPool(dcIpsStr)
	if err != nil {
		logError.Printf("parseCIDRPool: %v", err)
		return -2
	}

	globalCtx, globalCancel = context.WithCancel(context.Background())

	go func() {
		if err := runProxy(globalCtx, host, goPort, dcOptMap); err != nil {
			logError.Printf("runProxy error: %v", err)
		}
	}()

	return 0
}

//export StopProxy
func StopProxy() C.int {
	globalMu.Lock()
	defer globalMu.Unlock()

	if globalCancel == nil {
		return -1
	}

	globalCancel()
	globalCancel = nil
	globalCtx = nil

	// Reset stats for next run
	stats.connectionsTotal.Store(0)
	stats.connectionsWs.Store(0)
	stats.connectionsTcpFallback.Store(0)
	stats.connectionsHttpReject.Store(0)
	stats.connectionsPassthrough.Store(0)
	stats.wsErrors.Store(0)
	stats.bytesUp.Store(0)
	stats.bytesDown.Store(0)
	stats.poolHits.Store(0)
	stats.poolMisses.Store(0)

	// Reset blacklists and fail timers
	wsBlackMu.Lock()
	wsBlacklist = make(map[[2]int]bool)
	wsBlackMu.Unlock()

	dcFailMu.Lock()
	dcFailUntil = make(map[[2]int]float64)
	dcFailMu.Unlock()

	return 0
}

//export SetPoolSize
func SetPoolSize(size C.int) {
	n := int(size)
	if n < 2 {
		n = 2
	}
	if n > 16 {
		n = 16
	}
	poolSize = n
	if logInfo != nil {
		logInfo.Printf("Pool size set to %d", n)
	}
}

//export GetStats
func GetStats() *C.char {
	s := stats.Summary()
	return C.CString(s)
}

//export FreeString
func FreeString(p *C.char) {
	C.free(unsafe.Pointer(p))
}

// ---------------------------------------------------------------------------
// Standalone main (for testing; noop when built as c-shared)
// ---------------------------------------------------------------------------

func main() {
	// When built as c-shared, main() is not called.
	// For standalone testing:
	runtime.LockOSThread()

	initLogging(false)

	// Default DC IPs
	dcOptMap := map[int]string{
		2: "149.154.167.220",
		4: "149.154.167.220",
	}

	host := "127.0.0.1"
	port := defaultPort

	// Parse simple command line
	args := os.Args[1:]
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--port":
			if i+1 < len(args) {
				i++
				p, err := strconv.Atoi(args[i])
				if err == nil {
					port = p
				}
			}
		case "--host":
			if i+1 < len(args) {
				i++
				host = args[i]
			}
		case "-v", "--verbose":
			initLogging(true)
		case "--dc-ip":
			if i+1 < len(args) {
				i++
				entry := args[i]
				parsed, err := parseCIDRPool(entry)
				if err != nil {
					logError.Printf("%v", err)
					os.Exit(1)
				}
				for k, v := range parsed {
					dcOptMap[k] = v
				}
			}
		}
	}

	ctx, cancel := context.WithCancel(context.Background())

	// Graceful shutdown on SIGINT/SIGTERM
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigCh
		logInfo.Printf("Received signal %v, shutting down...", sig)
		cancel()
	}()

	if err := runProxy(ctx, host, port, dcOptMap); err != nil {
		logError.Printf("Fatal: %v", err)
		os.Exit(1)
	}
}

// Ensure mrand is used to avoid import errors
var _ = mrand.Int
var _ = big.NewInt