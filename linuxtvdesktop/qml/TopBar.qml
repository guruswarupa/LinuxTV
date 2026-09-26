import QtQuick 2.15

Item {
    id: root
    height: 64

    Row {
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: 32
        spacing: 24

        Text {
            text: "LinuxTV"
            color: theme.text
            font.family: theme.font_family
            font.pixelSize: 20
            font.bold: true
            anchors.verticalCenter: parent.verticalCenter
        }

        Rectangle {
            id: searchPill
            width: 260
            height: 40
            radius: 20
            // Translucent fill so the ambient background shows through
            // slightly, instead of a flat opaque pill.
            color: Qt.rgba(1, 1, 1, 0.12)
            border.width: backend.searchFocused ? 2 : 1
            border.color: backend.searchFocused ? theme.accent : Qt.rgba(1, 1, 1, 0.18)
            anchors.verticalCenter: parent.verticalCenter

            Behavior on border.color { ColorAnimation { duration: 150 } }

            Row {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                spacing: 8

                Text {
                    text: "⌕"
                    color: theme.text_muted
                    font.pixelSize: 16
                    anchors.verticalCenter: parent.verticalCenter
                }

                Item {
                    width: parent.width - 30
                    height: parent.height
                    anchors.verticalCenter: parent.verticalCenter

                    TextInput {
                        id: searchInput
                        anchors.fill: parent
                        verticalAlignment: TextInput.AlignVCenter
                        color: theme.text
                        font.family: theme.font_family
                        font.pixelSize: 15
                        clip: true
                        focus: backend.searchFocused

                        onTextChanged: backend.setSearchText(text)
                        onActiveFocusChanged: if (activeFocus) backend.setSearchFocused(true)

                        Keys.onDownPressed: (event) => {
                            backend.setSearchFocused(false)
                            event.accepted = true
                        }
                        Keys.onEscapePressed: (event) => {
                            if (searchInput.text.length > 0) {
                                searchInput.text = ""
                            } else {
                                backend.setSearchFocused(false)
                            }
                            event.accepted = true
                        }
                    }

                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Search apps"
                        color: theme.text_muted
                        font.family: theme.font_family
                        font.pixelSize: 15
                        visible: searchInput.text.length === 0
                    }
                }
            }
        }
    }

    Row {
        anchors.verticalCenter: parent.verticalCenter
        anchors.right: parent.right
        anchors.rightMargin: 32
        spacing: 20

        Text {
            text: backend.networkText
            color: theme.text_muted
            font.family: theme.font_family
            font.pixelSize: 14
            anchors.verticalCenter: parent.verticalCenter
        }

        Text {
            id: clock
            color: theme.text
            font.family: theme.font_family
            font.pixelSize: 16
            font.bold: true
            anchors.verticalCenter: parent.verticalCenter
            text: Qt.formatTime(new Date(), "hh:mm")

            Timer {
                interval: 1000
                running: true
                repeat: true
                onTriggered: clock.text = Qt.formatTime(new Date(), "hh:mm")
            }
        }

        Rectangle {
            width: 40
            height: 40
            radius: 20
            color: settingsMouse.containsMouse ? theme.surface_alt : "transparent"
            anchors.verticalCenter: parent.verticalCenter

            Text {
                anchors.centerIn: parent
                text: "⚙"
                color: theme.text
                font.pixelSize: 20
            }

            MouseArea {
                id: settingsMouse
                anchors.fill: parent
                hoverEnabled: true
                onClicked: backend.toggleSettingsPanel()
            }
        }
    }
}
