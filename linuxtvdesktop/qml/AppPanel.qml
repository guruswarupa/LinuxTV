import QtQuick 2.15

// Shell for every former QDialog popup (Add/Edit App, Delete confirm,
// Network, Bluetooth, Sound, Brightness, Remote Login, Auto-Open) -- one
// consistent in-scene modal card instead of a separate OS window, with a
// Loader picking the right body for backend.activePanel.
Item {
    id: root
    visible: opacity > 0
    opacity: backend.activePanel !== "" ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 150 } }

    // The app store is a browsing screen, not a small form: it takes the whole
    // window instead of the centered card the other panels use.
    readonly property bool fullscreen: backend.activePanel === "store"

    function titleFor(name) {
        switch (name) {
        case "addApp": case "editApp": return backend.panelData.title || "Add App"
        case "confirmDelete": return "Delete App"
        case "network": return "Network"
        case "bluetooth": return "Bluetooth"
        case "sound": return "Sound"
        case "brightness": return "Brightness"
        case "remoteLogin": return "Remote Login"
        case "autoOpen": return "Auto-Open"
        case "store": return "App Store"
        default: return ""
        }
    }

    function sourceFor(name) {
        switch (name) {
        case "addApp": case "editApp": return "AddEditAppPanel.qml"
        case "confirmDelete": return "ConfirmDeletePanel.qml"
        case "network": return "NetworkPanel.qml"
        case "bluetooth": return "BluetoothPanel.qml"
        case "sound": return "SoundPanel.qml"
        case "brightness": return "BrightnessPanel.qml"
        case "remoteLogin": return "RemoteLoginPanel.qml"
        case "autoOpen": return "AutoOpenPanel.qml"
        case "store": return "StorePanel.qml"
        default: return ""
        }
    }

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.55)
        MouseArea { anchors.fill: parent; onClicked: backend.panelAction("cancel", {}) }
    }

    Rectangle {
        id: card
        width: root.fullscreen ? parent.width : Math.min(560, parent.width - 80)
        height: root.fullscreen ? parent.height : Math.min(contentColumn.implicitHeight + 48, parent.height - 80)
        anchors.centerIn: parent
        radius: root.fullscreen ? 0 : 16
        color: theme.surface
        clip: true

        Flickable {
            id: flick
            anchors.fill: parent
            anchors.margins: root.fullscreen ? 40 : 24
            contentWidth: width
            contentHeight: contentColumn.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            Column {
                id: contentColumn
                width: flick.width
                spacing: 16

                Text {
                    id: titleText
                    width: parent.width
                    text: root.titleFor(backend.activePanel)
                    color: theme.text
                    font.family: theme.font_family
                    font.pixelSize: 20
                    font.bold: true
                    elide: Text.ElideRight
                }

                Loader {
                    id: bodyLoader
                    width: parent.width
                    // Fullscreen panels get all the room left under the title and status line.
                    height: root.fullscreen
                        ? Math.max(200, flick.height - titleText.height - statusText.height - contentColumn.spacing * 2)
                        : (item ? item.implicitHeight : 0)
                    source: root.visible ? root.sourceFor(backend.activePanel) : ""
                }

                Text {
                    id: statusText
                    width: parent.width
                    height: visible ? implicitHeight : 0
                    visible: (backend.panelData.status || "") !== ""
                    text: backend.panelData.status || ""
                    color: theme.text_muted
                    font.family: theme.font_family
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    focus: backend.activePanel !== ""
    Keys.onPressed: (event) => {
        if (event.key === Qt.Key_Escape || event.key === Qt.Key_Back) {
            backend.panelAction("cancel", {})
            event.accepted = true
        } else {
            event.accepted = false
        }
    }
}
