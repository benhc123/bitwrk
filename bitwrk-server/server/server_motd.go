//  BitWrk - A Bitcoin-friendly, anonymous marketplace for computing power
//  Copyright (C) 2014  Jonas Eschenburg <jonas@bitwrk.net>
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

package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"regexp"
	"strconv"
)

func handleMessageOfTheDay(w http.ResponseWriter, r *http.Request) {
	type motd struct {
		Text    string
		Warning bool
	}

	msg := getMessageOfTheDay(r)
	m := motd{Text: msg, Warning: false}

	bytes, err := json.Marshal(m)
	if err != nil {
		http.Error(w, "Error marshaling motd", http.StatusInternalServerError)
	} else {
		w.Header().Set("Content-Type", "application/json")
		w.Write(bytes)
	}
}

var uaRegexp = regexp.MustCompile("^BitWrkGoClient/([0-9]{1,4})\\.([0-9]{1,4})\\.([0-9]{1,4})")

func getMessageOfTheDay(r *http.Request) string {
	ua := r.Header.Get("User-Agent")
	matches := uaRegexp.FindStringSubmatch(ua)
	if matches == nil {
		return "Welcome, to the BitWrk network, stranger!"
	}

	major, _ := strconv.ParseInt(matches[1], 10, 16)
	minor, _ := strconv.ParseInt(matches[2], 10, 16)
	micro, _ := strconv.ParseInt(matches[3], 10, 16)

	if major > 0 || major == 0 && minor >= 3 {
		return fmt.Sprintf("Welcome to the BitWrk network!"+
			" Your client is up to date (version %d.%d.%d).", major, minor, micro)
	} else {
		return fmt.Sprintf("Welcome to the BitWrk network!"+
			" You are currently running client version %d.%d.%d."+
			" Please upgrade to 0.3.0 on <a href=\"http://bitwrk.net/\">the BitWrk homepage</a>.",
			major, minor, micro)
	}
}
