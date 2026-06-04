#!/bin/bash

git clone https://github.com/soheilrahsaz/cryptoNewsDataset.git

unar cryptoNewsDataset/csvOutput/cryptopanic_news.rar -o data/

rm -rf cryptoNewsDataset
