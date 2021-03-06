//  BitWrk - A Bitcoin-friendly, anonymous marketplace for computing power
//  Copyright (C) 2013  Jonas Eschenburg <jonas@bitwrk.net>
//
//  This program is free software: you can redistribute it and/or modify
//  it under the terms of the GNU General Public License as published by
//  the Free Software Foundation, either version 3 of the License, or
//  (at your option) any later version.
//
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
//  GNU General Public License for more details.
//
//  You should have received a copy of the GNU General Public License
//  along with this program.  If not, see <http://www.gnu.org/licenses/>.

package client

import (
	"bitwrk"
	"bitwrk/cafs"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"sync"
	"time"
)

type BuyActivity struct {
	Trade
}

func (m *ActivityManager) NewBuy(article bitwrk.ArticleId) (*BuyActivity, error) {
	now := time.Now()
	result := &BuyActivity{
		Trade: Trade{
			condition:  sync.NewCond(new(sync.Mutex)),
			manager:    m,
			key:        m.NewKey(),
			started:    now,
			lastUpdate: now,
			bidType:    bitwrk.Buy,
			article:    article,
			alive:      true,
		},
	}
	m.register(result.key, result)
	return result, nil
}

// Manages the complete lifecycle of a buy.
// When a bool can be read from interrupt, the buy is aborted.
func (a *BuyActivity) PerformBuy(log bitwrk.Logger, interrupt <-chan bool, workFile cafs.File) (cafs.File, error) {
	a.workFile = workFile.Duplicate()

	file, err := a.doPerformBuy(log, interrupt)
	if err != nil {
		a.lastError = err
	}
	a.alive = false
	return file, err
}

func (a *BuyActivity) doPerformBuy(log bitwrk.Logger, interrupt <-chan bool) (cafs.File, error) {
	if err := a.beginTrade(log, interrupt); err != nil {
		return nil, err
	}

	// draw random bytes for buyer's secret
	var secret bitwrk.Thash
	if _, err := rand.Reader.Read(secret[:]); err != nil {
		return nil, err
	}
	a.buyerSecret = &secret
	log.Printf("Computed buyer's secret.")

	// Get work hash
	var workHash, workSecretHash bitwrk.Thash
	workHash = bitwrk.Thash(a.workFile.Key())

	// compute workSecretHash = hash(workHash | secret)
	hash := sha256.New()
	hash.Write(workHash[:])
	hash.Write(secret[:])
	hash.Sum(workSecretHash[:0])

	// Start polling for transaction state changes in background
	abortPolling := make(chan bool)
	defer func() {
		abortPolling <- true // Stop polling when sell has ended
	}()
	go func() {
		a.pollTransaction(log, abortPolling)
	}()

	if err := SendTxMessageEstablishBuyer(a.txId, a.identity, workHash, workSecretHash); err != nil {
		return nil, fmt.Errorf("Error establishing buyer: %v", err)
	}

	if err := a.waitForTransactionPhase(log.New("establishing"), bitwrk.PhaseTransmitting, bitwrk.PhaseBuyerEstablished); err != nil {
		return nil, fmt.Errorf("Error awaiting TRANSMITTING phase: %v", err)
	}

	if err := a.interactWithSeller(log.New("transmitting")); err != nil {
		return nil, fmt.Errorf("Error transmitting work and receiving encrypted result: %v", err)
	}

	if err := a.waitForTransactionPhase(log, bitwrk.PhaseUnverified, bitwrk.PhaseTransmitting, bitwrk.PhaseWorking); err != nil {
		return nil, fmt.Errorf("Error awaiting UNVERIFIED phase: %v", err)
	}

	a.encResultKey = a.tx.ResultDecryptionKey

	if err := a.decryptResult(); err != nil {
		return nil, fmt.Errorf("Error decrypting result: %v", err)
	}
	
	if err := SendTxMessageAcceptResult(a.txId, a.identity); err != nil {
		return nil, fmt.Errorf("Failed to send 'accept result' message: %v", err)
	}
	
	a.waitWhile(func() bool { return a.tx.State == bitwrk.StateActive })

	return a.resultFile, nil
}

func (a *BuyActivity) testSellerForChunkedCapability(log bitwrk.Logger, client *http.Client) (bool, error) {
	req, err := newRequest("OPTIONS", *a.tx.WorkerURL, nil)
	if err != nil {
		return false, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return false, nil
	}

	decoder := json.NewDecoder(resp.Body)
	var caps struct {
		Adler32Chunking bool
	}
	err = decoder.Decode(&caps)
	if err != nil {
		return false, err
	}

	return caps.Adler32Chunking, nil
}

// Performs all buyer to seller conact.
// First queries the seller via HTTP OPTIONS whether chunked transmission is supported.
// If yes, a chunk list is transmitted, followed by data of missing work data chunks.
// Otherwise, work data is transferred linearly.
// The result is either an error or nil. In the latter case, a.encResultFile contains
// the result data encrypted with a key that the seller will hand out after we have signed
// a receipt for the encrypted result.
func (a *BuyActivity) interactWithSeller(log bitwrk.Logger) error {
    // Use a watchdog to make sure that all connnections created in the call time of this
    // function are closed when the transaction leaves the active state or the allowed
    // phases.
    // Transaction polling is guaranteed by the calling function.
	exitChan := make(chan bool)
	connChan := make(chan io.Closer)
	go a.watchdog(log, exitChan, connChan, func() bool {
		return a.tx.State == bitwrk.StateActive &&
			(a.tx.Phase == bitwrk.PhaseSellerEstablished ||
				a.tx.Phase == bitwrk.PhaseTransmitting ||
				a.tx.Phase == bitwrk.PhaseWorking)
	})
	defer func() {
		exitChan <- true
	}()

	st := NewScopedTransport()
	connChan <- st
	defer st.Close()
	scopedClient := NewClient(&st.Transport)
	
	chunked := false
	if a.workFile.IsChunked() {
		if supported, err := a.testSellerForChunkedCapability(log, scopedClient); err != nil {
			log.Printf("Failed to probe seller for capabilities: %v", err)
		} else {
			chunked = supported
			log.Printf("Chunked work transmission supported by seller: %v", chunked)
		}
	}

	var response io.ReadCloser
	var transmissionError error
	if chunked {
		response, transmissionError = a.transmitWorkChunked(log, scopedClient)
	} else {
		response, transmissionError = a.transmitWorkLinear(log, scopedClient)
	}
	if response != nil {
		defer response.Close()
	}
	if transmissionError != nil {
		return transmissionError
	}

	temp := a.manager.storage.Create(fmt.Sprintf("Buy #%v: encrypted result", a.GetKey()))
	defer temp.Dispose()

	if _, err := io.Copy(temp, response); err != nil {
		return err
	}
	if err := response.Close(); err != nil {
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}

	a.encResultFile = temp.File()

	if err := a.signReceipt(scopedClient); err != nil {
		return fmt.Errorf("Error signing receipt for encrypted result: %v", err)
	}

	return nil
}

func (a *BuyActivity) transmitWorkLinear(log bitwrk.Logger, client *http.Client) (io.ReadCloser, error) {
	// Send work to client
	pipeIn, pipeOut := io.Pipe()
	mwriter := multipart.NewWriter(pipeOut)

	// Write work file into pipe for HTTP request
	go func() {
		part, err := mwriter.CreateFormFile("work", "workfile.bin")
		if err != nil {
			pipeOut.CloseWithError(err)
			return
		}
		work := a.workFile.Open()
		log.Printf("Sending work data to seller [%v].", *a.tx.WorkerURL)
		_, err = io.Copy(part, work)
		work.Close()
		if err != nil {
			pipeOut.CloseWithError(err)
			return
		}
		log.Printf("Sending buyer's secret to seller.")
		err = mwriter.WriteField("buyersecret", a.buyerSecret.String())
		if err != nil {
			pipeOut.CloseWithError(err)
			return
		}
		err = mwriter.Close()
		if err != nil {
			pipeOut.CloseWithError(err)
			return
		}
		err = pipeOut.Close()
		if err != nil {
			pipeOut.CloseWithError(err)
			return
		}
		log.Printf("Work transmitted successfully.")
	}()

	return a.postToSeller(pipeIn, mwriter.FormDataContentType(), client)
}

func (a *BuyActivity) transmitWorkChunked(log bitwrk.Logger, client *http.Client) (io.ReadCloser, error) {
	if r, err := a.requestMissingChunks(log.New("request missing chunks"), client); err != nil {
		return nil, err
	} else {
		defer r.Close()
		numChunks := a.workFile.NumChunks()
		if numChunks > MaxNumberOfChunksInWorkFile {
			return nil, fmt.Errorf("Work file too big: %d chunks (only %d allowed).", numChunks, MaxNumberOfChunksInWorkFile)
		}
		wishList := make([]byte, int(numChunks+7)/8)
		if _, err := io.ReadFull(r, wishList); err != nil {
			return nil, fmt.Errorf("Error decoding list of missing chunks: %v", err)
		}
		return a.sendMissingChunksAndReturnResult(log.New("send work chunk data"), client, wishList)
	}
}

func (a *BuyActivity) requestMissingChunks(log bitwrk.Logger, client *http.Client) (io.ReadCloser, error) {
	// Send chunk list of work to client
	pipeIn, pipeOut := io.Pipe()
	defer pipeIn.Close()
	mwriter := multipart.NewWriter(pipeOut)

	// Write chunk hashes into pipe for HTTP request
	go func() {
		defer pipeOut.Close()
		if err := a.encodeChunkedFirstTransmission(log, mwriter); err != nil {
			pipeOut.CloseWithError(err)
			return
		}
		if err := mwriter.Close(); err != nil {
			pipeOut.CloseWithError(err)
			return
		}
		log.Printf("Work chunk hashes transmitted successfully.")
	}()

	if r, err := a.postToSeller(pipeIn, mwriter.FormDataContentType(), client); err != nil {
		return nil, fmt.Errorf("Error sending work chunk hashes to seller: %v", err)
	} else {
		return r, nil
	}
}

func (a *BuyActivity) sendMissingChunksAndReturnResult(log bitwrk.Logger, client *http.Client, wishList []byte) (io.ReadCloser, error) {
	// Send data of missing chunks to seller
	pipeIn, pipeOut := io.Pipe()
	defer pipeIn.Close()
	mwriter := multipart.NewWriter(pipeOut)

	// Write work chunks into pipe for HTTP request
	go func() {
		defer pipeOut.Close()
		if part, err := mwriter.CreateFormFile("chunkdata", "chunkdata.bin"); err != nil {
			pipeOut.CloseWithError(err)
			return
		} else if err := cafs.WriteRequestedChunks(a.workFile, wishList, part); err != nil {
			pipeOut.CloseWithError(err)
			return
		}
		if err := mwriter.Close(); err != nil {
			pipeOut.CloseWithError(err)
			return
		}
		log.Printf("Missing chunk data transmitted successfully.")
	}()

	if r, err := a.postToSeller(pipeIn, mwriter.FormDataContentType(), client); err != nil {
		return nil, fmt.Errorf("Error sending work chunk data to seller: %v", err)
	} else {
		return r, nil
	}
}

func (a *BuyActivity) encodeChunkedFirstTransmission(log bitwrk.Logger, mwriter *multipart.Writer) (err error) {
	part, err := mwriter.CreateFormFile("a32chunks", "a32chunks.bin")
	if err != nil {
		return
	}
	log.Printf("Sending work chunk hashes to seller [%v].", *a.tx.WorkerURL)
	err = cafs.WriteChunkHashes(a.workFile, part)
	if err != nil {
		return
	}
	log.Printf("Sending buyer's secret to seller.")
	err = mwriter.WriteField("buyersecret", a.buyerSecret.String())
	if err != nil {
		return
	}
	return mwriter.Close()
}

func (a *BuyActivity) postToSeller(postData io.Reader, contentType string, client *http.Client) (io.ReadCloser, error) {
	if req, err := newRequest("POST", *a.tx.WorkerURL, postData); err != nil {
		return nil, fmt.Errorf("Error creating transmit request: %v", err)
	} else {
		req.Header.Set("Content-Type", contentType)

		if resp, err := client.Do(req); err != nil {
			return nil, err
		} else if resp.StatusCode != http.StatusOK {
			buf := make([]byte, 1024)
			n, _ := resp.Body.Read(buf)
			buf = buf[:n]
			resp.Body.Close()
			return nil, fmt.Errorf("Seller returned bad status '%v' [response: %#v]", resp.Status, string(buf))
		} else {
			return resp.Body, nil
		}
	}
}

// Signs a receipt for the encrypted result that the seller can use to
// prove that the result was transmitted correctly. In exchange, we get the
// key to unlock the encrypted result.
func (a *BuyActivity) signReceipt(client *http.Client) error {
	encresulthash := a.encResultFile.Key().String()
	if sig, err := a.identity.SignMessage(encresulthash, rand.Reader); err != nil {
		return err
	} else {
		a.encResultHashSig = sig
	}

	formValues := url.Values{}
	formValues.Set("encresulthash", encresulthash)
	formValues.Set("encresulthashsig", a.encResultHashSig)

	if resp, err := client.PostForm(*a.tx.WorkerURL, formValues); err != nil {
		return err
	} else if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("Error sending receipt for encrypted result: %v", resp.Status)
	}

	return nil
}

func (a *BuyActivity) decryptResult() error {
	block, err := aes.NewCipher(a.encResultKey[:])
	if err != nil {
		return err
	}

	temp := a.manager.GetStorage().Create(fmt.Sprintf("Buy #%v: result", a.GetKey()))
	defer temp.Dispose()

	encrypted := a.encResultFile.Open()
	defer encrypted.Close()

	// Create OFB stream with null initialization vector (ok for one-time key)
	var iv [aes.BlockSize]byte
	stream := cipher.NewOFB(block, iv[:])

	reader := &cipher.StreamReader{S: stream, R: encrypted}
	_, err = io.Copy(temp, reader)
	if err != nil {
		return err
	}

	if err := temp.Close(); err != nil {
		return err
	}

	a.resultFile = temp.File()

	return nil
}
